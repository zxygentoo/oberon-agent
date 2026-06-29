# Real-serial transport: findings & fixes (HW-confirmed)

> First time `oat` + `AgentTool` were driven over a **real UART** instead of the
> emulator's FIFO. It works now — but getting there exposed transport gaps the lossless
> FIFO had hidden, and corrected one assumption along the way. **Status: fixed and
> confirmed on real hardware** (Nexys 4, Project Oberon 2013); changes still uncommitted
> (pending review).

## Context

Driving a live **Project Oberon 2013 on real FPGA silicon** (a Hardcaml RISC5 port on a
Digilent Nexys 4) via `oat`, to debug the system from the inside. The link:

```
oat (host) <-> /dev/ttyUSB1 <-> FT2232 USB-UART <-> FPGA RS232R/RS232T <-> AgentTool (Oberon)
```

19200 baud, 8N1, **no flow control**. The FPGA UART is Wirth's OberonStation `RS232R`/
`RS232T` (a faithful, cosim-verified port): a **single-byte receive register, no FIFO**.
`AgentTool` autostarts on boot (an `Oberon.Mod` patch loads it), so no keyboard is needed.
The agent's poll runs as **one cooperative task** in `Oberon.Loop` — it only reads the wire
when scheduled.

## The core problem: one-byte register, no flow control

The wire protocol was written for a **lossless, back-pressured** transport. The emulator's
FIFO is exactly that: bytes never corrupt, and the emulated UART only advances when Oberon
*reads*, so a slow reader can never overrun. A real UART has **neither** property: with a
one-byte register and no flow control, if a second byte arrives before the agent reads the
first, the first is **silently overwritten** (an overrun). That bites in **two distinct
places**, each with its own fix — plus a latent hang.

### Overrun 1 — the bulk PUT *payload*, at file-sector boundaries (device-side fix)

A module `write` of a ~685-byte file landed **corrupted** — `read`-back and the device's own
compiler agreed a `)` had been dropped near byte 677. Not random noise: a **deterministic
overrun at a file-sector boundary**. The corruptions sat just past **672 = SectorSize −
HeaderSize = 1024 − 352** (a file header sector's data capacity, from `FileDir`). The cause:
`AgentProtocol.DoPut` called `Files.WriteByte` **once per received byte, inside the receive
loop**, and at byte 672 `WriteByte` crosses into the next sector → `WriteBuf` →
`Disk.PutSector`, **a real SD write over SPI**. That write stalls *longer than a byte-time*
(longer than even an 8 ms paced gap), so the one-byte `RS232R` register **overruns** — every
time, at the boundary. (`GET`/read-back is fine: there the stall is on the *send* side, host
reads buffered. `DoCall`/`DoEdit` receive into RAM, not disk. So `DoPut` was the only
vulnerable path.)

**Fix:** `DoPut` buffers the whole payload into RAM in a tight `Rec` loop (no disk I/O
between bytes), *then* writes the file. The slow `Disk.PutSector`s happen after the wire is
drained, so the register can't overrun. **HW-confirmed:** a 1775-byte module (past the 672
boundary) now round-trips **byte-identical**.

### Overrun 2 — the *request frame's* first byte, back-to-back (host-side fix)

This is the one the FIFO completely hid, and the assumption it corrected. The device buffers
the payload, but the **small request frames** (sync/op/name/len, received byte-by-byte
*before* any buffering) are still at the mercy of the cooperative poll. Fire a request with
no gap — right as the agent is finishing its previous reply and hasn't returned to listening
— and the frame's **first byte overruns** before the poll grabs it; the frame desyncs. A
human spacing commands by seconds never hits it; an **automated driver firing them
back-to-back always does** (observed: `check` works once after a fresh boot, then an
immediately-following `list-modules` desyncs the agent until reset).

**Fix:** `oat --char-delay-us` — the inter-byte / "character delay" knob standard serial
terminals expose (TeraTerm "transmit delay", minicom "character pacing"), **default
1000 µs** ≈ 2× the ~520 µs byte-time at 19200, so the poll reliably catches each byte.
**HW-confirmed:** at the default, back-to-back `check`/`list-modules`/`list-files`/`write`/
`delete` are all reliable.

> **Correction to an earlier note.** This doc previously called pacing a useless stopgap
> ("the device fix removes the need for it; default 0"). Wrong: the device buffering covers
> only Overrun 1 (the payload); Overrun 2 (the request frame) genuinely needs host-side
> throttling. Hence the rename `--pace-us` → `--char-delay-us` and the nonzero default.

### Latent hang — a lost byte must not freeze the OS (device-side safety net)

`AgentProtocol.Rec` was an **unbounded** busy-wait:

```
PROCEDURE Rec(VAR x: BYTE);
BEGIN REPEAT UNTIL SYSTEM.BIT(stat, 0); SYSTEM.GET(data, x) END Rec;
```

A genuinely lost byte would spin forever → `Task` never returns → the **whole `Oberon.Loop`
freezes**, recoverable only by hardware reset.

**Fix:** `Rec` is bounded (spin ≤ `recLim`); on timeout it clears an `rxOk` flag that
short-circuits the rest of the frame; every handler checks `rxOk` before committing/replying;
`Task` resets it per frame and resyncs on the next `sync`. A lost byte now **aborts one
frame** instead of freezing the system. With `--char-delay-us` preventing the overrun up
front, this is the defensive backstop — rarely exercised, but it's what keeps a one-off line
glitch from bricking the session.

## Host-side `oat` fixes (needed for *any* real serial port)

The transport had only ever run against emulator FIFOs. Three real-UART gaps, in
`oat/src/{transport,cli}.rs`:

1. **Baud was never set.** `make_raw()` gives 8N1 but leaves the line *speed* untouched —
   whatever the port last had (we measured the FTDI sitting at 115200). `--baud`
   (default 19200) → `Termios::set_speed`.
2. **No input flush between invocations.** A late/lost reply leaves bytes in the OS buffer
   that the next run reads first, shifting every field ("no version string", empty
   `list-files`). `drain_stale()` before each send.
3. **`--char-delay-us`** — Overrun 2's fix, above (default 1000; 0 for the lossless FIFO
   path, which ignores it).

## Root cause, in one line

The link itself is clean. The protocol was correct for a lossless, back-pressured FIFO and
met a real UART's single-byte register two ways — a slow **disk write** mid-receive overran
the *payload* (device-side buffering), and a cooperative poll missed the *request frame's*
first byte on back-to-back commands (host-side char-delay) — while an unbounded `Rec` turned
any genuine lost byte into a hang (bounded `Rec`).

## Implementation notes

`DoPut` uses a fixed `maxPut` (64 KiB) RAM buffer — `POINTER TO ARRAY OF BYTE` + `NEW(p, len)`
isn't accepted by this Oberon-07 compiler; `maxPut` doubles as the PUT size cap (a larger
PUT, or a corrupt length, gets `stError`). No wire-format change, no checksum/retry: once both
overruns are fixed the link isn't lossy, so integrity armor would be defense-in-depth, not a
cure — revisit only on corruption that is *not* at a sector boundary.

## Status — done, HW-confirmed

- **`oat`**: `--baud`, `drain_stale`, `--char-delay-us` (default 1000) — 51 unit tests pass;
  back-to-back commands reliable on hardware at the default.
- **`AgentProtocol`**: `DoPut` payload buffering + bounded `Rec`/`rxOk` (with the `rxOk` guard
  threaded through `DoGet`/`DoCall`/`DoEdit`) — both images compile, PO **29/29**, EO
  **33/33**; bulk write byte-identical on hardware past the 672 B boundary.
- **Checksum + retry: not done** (not needed — see above).
- **Uncommitted** (pending review); the HW results are with the current images flashed.
