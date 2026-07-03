# Real-serial transport: findings & fixes (HW-confirmed)

> First time `oat` + `AgentTool` were driven over a **real UART** instead of the
> emulator's FIFO. It works now — but getting there exposed transport gaps the lossless
> FIFO had hidden, and corrected one assumption along the way. The first pass (below) is
> **committed** (`2d943ac`). A later **stress-testing pass** (2026-06-30, both variants on
> silicon) then measured the link properly and corrected the headline claim: char-delay
> alone is *not* enough on Project Oberon. **Status: first pass landed; reliability gap
> quantified; host-side retry implemented (uncommitted)** — see
> [Second pass](#second-pass--stress-testing-the-link-2026-06-30).

## Context

Driving a live **Project Oberon 2013 on real FPGA silicon** (a Hardcaml RISC5 port on a
Digilent Nexys 4) via `oat`, to debug the system from the inside. The link:

```
oat (host) <-> /dev/ttyUSB1 <-> FT2232 USB-UART <-> FPGA RS232R/RS232T <-> AgentTool (Oberon)
```

115200 baud on the current 60 MHz FPGA build (19200 on the faithful 25 MHz build these
measurements were first made at), 8N1, **no flow control**. The FPGA UART is Wirth's OberonStation `RS232R`/
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
terminals expose (TeraTerm "transmit delay", minicom "character pacing"), then **default
1000 µs** ≈ 2× the ~520 µs byte-time at 19200, so the poll reliably catches each byte.
(Since retuned to **600 µs** for the 60 MHz build — the binding constraint is the device
poll window, not the byte-time, and 600 runs 100% back-to-back with retries.)

> **Correction (stress pass, 2026-06-30).** "All reliable at the default" was based on a
> handful of hand-run commands. Measured properly, the default is reliable *on Extended
> Oberon* (0/30 desyncs) but **leaves a ~13 % residual desync rate on Project Oberon** —
> char-delay cannot remove it because the failure is a device-side `Oberon.Loop` stall, not
> an inter-byte spacing problem. See the [Second pass](#second-pass--stress-testing-the-link-2026-06-30).
> The mechanism here is right and char-delay is still needed (full line rate = ~70 % loss);
> it just isn't *sufficient* on PO.

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
   (then default 19200; now 115200, the 60 MHz build's rate) → `Termios::set_speed`.
2. **No input flush between invocations.** A late/lost reply leaves bytes in the OS buffer
   that the next run reads first, shifting every field ("no version string", empty
   `list-files`). `drain_stale()` before each send.
3. **`--char-delay-us`** — Overrun 2's fix, above (now default 600; 0 for the lossless
   FIFO path, which ignores it).

## Root cause, in one line

The link itself is clean. The protocol was correct for a lossless, back-pressured FIFO and
met a real UART's single-byte register two ways — a slow **disk write** mid-receive overran
the *payload* (device-side buffering), and a cooperative poll missed the *request frame's*
first byte on back-to-back commands (host-side char-delay) — while an unbounded `Rec` turned
any genuine lost byte into a hang (bounded `Rec`).

## Implementation notes

`DoPut` uses a fixed `maxPut` (64 KiB) RAM buffer — `POINTER TO ARRAY OF BYTE` + `NEW(p, len)`
isn't accepted by this Oberon-07 compiler; `maxPut` doubles as the PUT size cap (a larger
PUT, or a corrupt length, gets `stError`).

> **Correction (stress pass).** This note originally said "no checksum/retry: once both
> overruns are fixed the link isn't lossy." The premise is wrong for Project Oberon — the
> link *is* lossy there (~13 % of request frames desync, independent of size; see below).
> The integrity half holds (a frame that completes is byte-identical, so no checksum is
> needed), but the **reliability** half needs **host-side retry** — now implemented, not "not
> needed." See the [Second pass](#second-pass--stress-testing-the-link-2026-06-30).

## Second pass — stress testing the link (2026-06-30)

The first pass was validated by hand. This pass built a transport-agnostic stress harness
(`scratchpad/stress.sh` — size-sweep integrity, a char-delay desync sweep, and a mixed-op
soak) and ran it against the **emulator FIFO (PO + EO)** and the **real board (PO, then EO
off a second SD card)**. The numbers reframe the problem.

### What's actually wrong: reliability, not integrity

Every observed hardware failure is a **request that desyncs and times out** — *never* a
corrupted byte. When a frame completes it is byte-identical, at every size (1 B … 8 KiB,
across the 672 / 1024 / 1696 sector boundaries). So `DoPut`'s RAM buffering fully closed
Overrun 1; what remains is **Overrun 2, and it is not rare**.

The desyncs are **size-independent**: a 1-byte write fails as often as an 8 KiB one, and the
failing sizes differ run to run. That rules out payload corruption and points squarely at the
*first byte* of the request frame.

### Mechanism, sharpened: only the sync byte is exposed

`AgentProtocol.Task` is **one cooperative `Oberon.Loop` tick**. It acts only if a byte is
*already* sitting in the one-byte `RS232R` register when the tick runs (`IF SYSTEM.BIT(stat,
0)`). It reads `sync`, then `op`, then the handler reads the **rest of the frame with the
busy-wait `Rec`** (spins up to `recLim` ≈ 1 s per byte). So:

- **Only the first byte (`sync`) is vulnerable** — it must survive in the register until the
  *next* `Task` tick. Every later byte is caught by the dedicated in-handler spin, at any
  speed. (This is also why size and inter-byte char-delay barely move the needle.)
- `sync` is lost whenever one `Oberon.Loop` period — jittered by GC and the other installed
  tasks — exceeds the gap before the next byte. **A GC pause can exceed any practical
  char-delay**, so fixed pacing has an irreducible failure floor.

### The data

Back-to-back `check`, 30 trials per char-delay (µs), failure rate:

| char-delay | 0 | 500 | **1000** (default) | 1500 | 2000 | 3000 | 5000 |
|-----------:|---:|----:|:---:|----:|----:|----:|----:|
| **Project Oberon**  | 70 % | 40 % | **13 %** | 10 % | 0 % | 13 % | 3 % |
| **Extended Oberon** | 77 % | 30 % | **0 %**  | 0 %  | 0 % | 0 %  | 0 % |

- Full line rate (0 µs) is a 70–77 % loss → **char-delay is genuinely needed**; the knee is
  ~1000 µs. Above it, PO plateaus on a noisy ~0–13 % GC-stall floor; **EO has effectively no
  floor** (steadier loop / shorter GC pauses). Same shared `AgentProtocol.Mod`, so the gap is
  the OS's loop timing, not the protocol.
- Emulator (lossless, back-pressured FIFO): **0 failures at every delay, both variants** —
  it cannot reproduce the fault, which is exactly why it hid Overrun 2 originally.
- Integrity sweep: **16/16 byte-identical** on PO, EO, and emulator.
- Mixed-op soak at the default: emulator 30/30, EO 30/30; (PO not soaked — its per-request
  floor is already characterized by the table).

### The fix the data points to: host-side retry

The device **already self-recovers per frame** — `Rec`'s timeout clears `rxOk`, the handlers
bail without replying, and the next `Task` tick resyncs on a fresh `sync`. Every single
failure above logged `recovered=yes`. So the host just has to **retry on timeout**:

| | raw (1 attempt) | with retry (≤3 attempts) |
|---|---|---|
| PO, 40 back-to-back | 33/40 (82.5 %) | **40/40** (7 took a 2nd try, 0 a 3rd) |
| EO, 40 back-to-back | 40/40 | 40/40 |
| PO, 20 write+read round-trips | 16/20 first-try | **20/20 byte-identical** |

One retry clears essentially everything; two is belt-and-suspenders.

> **Caveat — the desync rate is load-dependent.** A *freshly booted, quiet* PO measures far
> better than a churned one: re-validating on a just-reprogrammed PO, back-to-back at the
> default was **50/50 raw** (vs 33/40 earlier, after heavy testing had churned the heap). The
> GC-stall floor scales with heap pressure, so it isn't something a fixed char-delay can
> bound — which is exactly why a retry safety net is the right shape: it escalates only when
> the line is actually struggling.

### The fix, implemented (uncommitted)

Host-side retry now lives in `oat` as a **`Retry` decorator on the `Request` seam**
(`oat/src/retry.rs`) — kept out of `transport.rs` so I/O and reliability policy stay separate,
and unit-testable in isolation:

1. **`--retries N`** (default **2**) re-sends a request on a transport **desync** (`Timeout`
   or `BadSync`) up to N extra attempts. Tool-level statuses come back as `Ok(Response)` and
   are never retried; genuine line failures (`Eof`, `Io`) fail fast. `--char-delay-us` stays at
   1000 (the knee that kills the bulk loss); retry mops up the residual floor.
2. **Forced 0 on the FIFO/emulator path** — lossless and back-pressured, so a timeout there is
   a real hang, not worth waiting out N more timeouts (mirrors how char-delay is zeroed for
   FIFOs; keeps emulator/test timing unchanged).
3. **Idempotency — retries every opcode uniformly, and that's safe here.** The lossy direction
   is device RX *only*: a desync drops a byte *before* the device acts (`DoPut`/`DoEdit`/
   `DoCall` all check `rxOk` before `Files.Register` / the splice / running the executor), and
   the host's own RX is OS-buffered (no single-byte overrun). So the one case that would make a
   re-send non-idempotent — device acts, then its *reply* is lost, and a retried `EDIT` re-runs
   into `stNoMatch` — does not occur on this asymmetric link. (Considered and deliberately not
   armored against; the rationale is documented in `retry.rs`.)

**Validated** — `oat` **57 unit tests** (6 new for `Retry`), clippy clean, PO **29/29** + EO
**33/33** emulator integration unchanged (FIFO → 0 retries). On hardware, with the shipping
binary: PO size-sweep **16/16** + soak **30/30** at the default; and retry demonstrably
recovers the forced-failure regime (PO at char-delay 0: **12/40 → 37/40** with `--retries 5`;
EO at char-delay 400: **40 % → 82.5 %**).

### Still open

- **Promote the harness — done:** `test/stress.sh` is the opt-in hardware/FIFO bench
  (`test/stress.sh --serial /dev/ttyUSB1`), so the desync rate stays measurable, not anecdotal.
  Not wired into `make test` (that stays deterministic).
- **char-delay default** — EO needs none above the knee; PO needs the knee. 1000 stays a sane
  shared default; the per-variant reality is documented here.
- **Commit** — the second-pass changes are uncommitted pending review.

### Operational notes (Nexys 4)

- The bitstream is **volatile** — a power-cycle wipes it and the serial line goes silent (0
  bytes, not a protocol error). Reload (and hard-reset the SoC) with:
  `cd ~/Projects/oberon-risc-hardcaml && vivado -mode batch -source boards/nexys-4/program.tcl`.
- **First boot right after a power-cycle can be marginal**: EO came up, answered `check` once,
  then died while idle (a whole stress run was dead-on-arrival, `recovered=no` throughout). A
  second re-program gave a clean, stable boot. If a freshly powered board answers then dies,
  **re-program before concluding anything** about the software.

## Status

- **First pass — committed (`2d943ac`), HW-confirmed:**
  - **`oat`**: `--baud`, `drain_stale`, `--char-delay-us` (default 1000) — 51 unit tests pass.
  - **`AgentProtocol`**: `DoPut` payload buffering + bounded `Rec`/`rxOk` (guard threaded
    through `DoGet`/`DoCall`/`DoEdit`) — both images compile, PO **29/29**, EO **33/33**;
    bulk write byte-identical on hardware past the 672 B boundary.
- **Second pass — stress-tested, diagnosed, fixed; uncommitted:** integrity confirmed solid;
  reliability quantified (PO ~13 % residual desync at the default — load-dependent — EO ~0 %);
  **host-side retry implemented** (`Retry` decorator + `--retries`, default 2) and validated on
  hardware (PO 12/40 → 37/40 in the forced regime; integrity/soak clean). `test/stress.sh`
  added as the opt-in link bench.
- **Checksum: still not needed** (completed frames are byte-identical). **Retry: done**
  (supersedes the earlier "not needed").
