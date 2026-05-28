# puck — Spec (v0.1, draft)

> Working draft. Intentionally incomplete — we iterate on it.

## 1. Project goal

Build a coding agent that operates **on and within Project Oberon (2013 edition)**,
exploiting Oberon's defining property — modules can be compiled and loaded/unloaded in the
*running* system — so the agent can modify the live system from the inside. Modifying the
compiler toolchain itself (ORP/ORG/ORB/ORS) is an explicit **stretch goal**.

North star: *the system modifies itself from within.* Design decisions should preserve that
property.

## 2. Key research findings

Grounded in the PO2013 reference sources (`op2013-src/`) and the Rust emulator / host tools
(`../oberon-risc-emu-rs`, built into `bin/`).

- **Channel.** The emulator does not emulate the network, so the **serial line is the only
  I/O channel** out of Oberon: `RS232 ↔ host proxy ↔ HTTPS ↔ LLM`.
- **Serial contract** (confirmed in emulator `raw_serial.rs`): status **bit 0 = rx-ready,
  bit 1 = tx-ready**, byte-at-a-time, at MMIO `data=-56` / `stat=-52` — identical to what
  `PCLink1.Mod` already polls. ⇒ the Oberon server reuses PCLink1's `Rec`/`Send` verbatim.
  The proxy attaches via `--serial-in` / `--serial-out` (Unix fds: a PTY in raw mode, or
  two FIFOs).
- **Command invocation is built in.** `Oberon.Call("Mod.Proc", res)` loads the module then
  runs the command. `Oberon.Par.text` / `.pos` is the parameter channel (every command
  scans `Par.text` via a `Texts.Scanner`). Command output funnels to **`Oberon.Log`**
  (a `Texts.Text`) — capture the delta appended during the call.
- **Errors are already text in the Log.** `ORS.Mark` writes `pos <N> <msg>` (N = char
  offset; max 25 errors). `System.Trap` writes `pos <N> TRAP<w> in <Mod> at <addr>`. The
  proxy maps char-offset → line:col against the source.
- **Source format.** Plain ASCII source compiles — `Texts.Open` switches on first byte
  `TextTag = 0F1X` (formatted) else the ASCII path. The agent writes plain text. Files
  written by Oberon (`Texts.Close`) get the 0F1X header → strip with `ob2unix`.
- **File I/O.** PUT/GET = PCLink1's `Rec`/`Send` byte transfer (format-agnostic,
  binary-safe). The `Edit` module is viewer/display-coupled — wrong for a headless agent.
- **Trap survival is the one hard part.** A trapping `CALL` would kill the server (the
  default trap handler removes the active task — see §5 for the mechanism and fix). Norebo
  does *not* help (one-shot per process; it SysReqs the host and halts) — only its
  trap-*decode* prologue is reusable. COMPILE itself is safe (errors are logged, not trapped).
- **Norebo / SysReq (alternative model, exists).** Norebo runs headless one-shot commands
  with a host-syscall ABI (host-mediated Files, Log over RS232); it's how `build-image`
  compiles. Kept as the **host-side ground-truth compile / image-rebuild path** (bootstrap
  escape + rollback), *not* as the executor.
- **Memory.** `--mem MEGS` lifts the 1 MB default (fixed memory-map ceiling, unverified).
  Not a Phase-1 concern (the host holds the conversation).

## 3. Decisions & phasing (locked 2026-05-28)

- **Execution = Model A:** live PO2013 + a serial `Agent.Mod` server (persistent,
  self-modifying) — not the Norebo one-shot model.
- **Two phases:** (1) tools on Oberon, agent loop on the **host**; (2) move the loop into
  Oberon (the romance / stretch). The host proxy never goes away — it always owns HTTPS +
  JSON, and the same wire format serves both phases (Phase 1 carries tool-calls; Phase 2
  carries LLM request/response). Beyond both: compiler-toolchain self-modification.
- **Oberon never parses JSON:** a custom wire format crosses the serial line; the proxy
  translates it ⇄ JSON.
- **Languages:** Oberon-07 on the device; **Python** proxy for now (revisit a Rust/TS/JS
  port after Phase 2), `uv`-managed with thin deps (`openai` + `rich`; see §4.6). A future
  imaging/storage layer (rollback/history) would be Rust or Zig, decided later, independent
  of the proxy.
- **Edit semantics:** `edit_file` = str_replace (proxy-side: GET fresh → replace → PUT);
  `write_file` for create/overwrite.

## 4. Phase 1 system design

**Execution = Model A:** live PO2013 + a serial `Agent.Mod` server. Persistent, stateful,
self-modifying.

**Two surfaces, bridged by the proxy:**
- *Wire protocol (Oberon ↔ proxy):* minimal, dumb, robust — three ops `PUT` / `GET` / `CALL`.
- *Agent API (proxy ↔ LLM):* rich, explicit, named tools with structured results.

Everything below is grounded in the PO2013 sources and validated against `bin/build-image`
(see §6). Source line:col references are to `op2013-src/`.

### 4.1 Serial byte layer (Oberon ↔ host)

The only primitives are PCLink1's `Rec`/`Send`, reused verbatim (MMIO `data=-56`,
`stat=-52`):

```oberon
PROCEDURE Rec(VAR x: BYTE);  BEGIN REPEAT UNTIL SYSTEM.BIT(stat, 0); SYSTEM.GET(data, x) END Rec;
PROCEDURE Send(x: BYTE);     BEGIN REPEAT UNTIL SYSTEM.BIT(stat, 1); SYSTEM.PUT(data, x) END Send;
```

- The status gate matters: the emulator's `read_data` returns **0 on no-data/EOF**, so any
  read *must* first spin on `stat` bit 0 (`Rec` does). `Send` spins on bit 1.
- We reuse only these byte primitives — **not** PCLink1's `REQ`/`REC`/`SND` block protocol
  with per-block `ACK`/`NAK`. puck defines its own, simpler length-prefixed framing (§4.2).
- **Multi-byte integers are unsigned little-endian** (LSB first), sent as 4 `Send` calls:
  ```oberon
  PROCEDURE SendInt(n: INTEGER); (* 4 bytes LE *)
  BEGIN Send(n MOD 100H); Send(n DIV 100H MOD 100H); Send(n DIV 10000H MOD 100H); Send(n DIV 1000000H MOD 100H) END;
  PROCEDURE RecInt(VAR n: INTEGER);  VAR a,b,c,d: BYTE;
  BEGIN Rec(a); Rec(b); Rec(c); Rec(d); n := a + b*100H + c*10000H + d*1000000H END;
  ```
- **No per-byte flow control.** The host pipe/PTY buffer (tens of KB) decouples host write
  speed from Oberon's read speed; a module source is a few KB, well under that. Once the
  task starts a frame it busy-reads to completion (it does not yield to `Oberon.Loop`
  mid-frame). If a payload ever outgrows the OS buffer, add PCLink1-style windowed `ACK`s as
  hardening. A truncated/garbled frame hangs the read until the host supervisor resets the
  emulator (§4.6).

### 4.2 Wire protocol (framing)

Host is master: it writes one complete **REQUEST**, then reads one **RESPONSE**. The Agent
task — polled by `Oberon.Loop` — begins reading when `stat` bit 0 is set, then reads the
whole frame.

```
REQUEST
  sync   : 1   = 0A5H               (* frame start + version tag *)
  op     : 1   = 1 PUT | 2 GET | 3 CALL
  op = PUT  (create/overwrite a file):
      nameLen: 1            (1..31)
      name   : nameLen      (ASCII)
      dataLen: 4   (LE)
      data   : dataLen      (raw file bytes)
  op = GET  (read a file):
      nameLen: 1
      name   : nameLen
  op = CALL (run "Mod.Proc"):
      cmdLen : 1
      cmd    : cmdLen       ("Mod.Proc")
      parLen : 4   (LE)
      par    : parLen       (parameter text; scanned via Oberon.Par)

RESPONSE
  sync      : 1 = 05AH
  status    : 1 = 0 ok | 1 not-found | 2 trapped | 3 error
  payloadLen: 4 (LE)
  payload   : payloadLen bytes
      PUT  -> empty
      GET  -> file bytes (raw; may carry the 0F1X header)
      CALL -> Oberon.Log delta (bytes appended to the Log during the call)
```

The `sync` bytes (`0A5H` request, `05AH` response) let either side detect a desync and act
as a 1-byte version tag for later. If the Agent reads a first byte that is not `0A5H` it is
out of frame → it drains rx until idle and waits for the next `0A5H`.

**`status` ← `Oberon.Call`'s `res`** (res codes are authoritative in `Modules.Mod`; note the
code disagrees with its own comment — these are the *code* values):

| `res` | meaning | wire `status` |
|---|---|---|
| 0 | ok | 0 |
| 1 | name invalid / file (`.rsc`) not found | 1 |
| 5 | no `.` in name, or command not found in module | 1 |
| 2 | bad symbol-file/version key | 3 |
| 3 | import key conflict (stale importer) | 3 |
| 4 | corrupted object file | 3 |
| 7 | no module space | 3 |
| — | runtime trap during the call | 2 *(post-hardening, §5)* |

> **Compile is special:** `ORP.Compile` is a normal command that only *logs* diagnostics, so
> a source with errors still returns **`res = 0` → status 0**. The command ran; the errors
> are in the payload. The proxy's `compile` tool decides pass/fail by **parsing the payload**
> (§4.3), never by the wire status.

**Log-delta capture (CALL).** `Oberon.Log` is a `Texts.Text`; `Texts.Append` always inserts
at `Log.len`. So the dispatcher records `beg := Log.len` before `Oberon.Call`, then after the
call streams `[beg, Log.len)` as the payload:

```oberon
beg := Oberon.Log.len; Oberon.Call(cmd, res); end := Oberon.Log.len;
Texts.OpenReader(R, Oberon.Log, beg);
WHILE Texts.Pos(R) < end DO Texts.Read(R, ch); Send(ORD(ch)) END
```
(The Log grows unbounded; if it gets large the Agent may `Texts.Delete(Oberon.Log, 0, len, buf)` between turns.)

### 4.3 Agent tools (proxy ↔ LLM)

Rich, explicit, named — the proxy implements each on top of the three wire ops. Collapsing
to one generic verb is explicitly *not* a goal.

| tool | params | returns | proxy implementation | wire |
|---|---|---|---|---|
| `read_file` | `path` | `{content}` / not_found | GET; if first byte `0F1H` strip Texts header (`ob2unix`); CR→LF | GET |
| `write_file` | `path, content` | `{ok}` | LF→CR; PUT plain ASCII (create/overwrite) | PUT |
| `edit_file` | `path, old, new` | `{ok, replaced}` / not_unique / not_found | GET fresh → str_replace (must be unique) → PUT | GET+PUT |
| `delete_file` | `path` | `{ok}` / not_found | CALL `System.DeleteFiles`; parse Log (`… deleting`/` failed`) | CALL |
| `list_files` | `[prefix]` | `{files:[{name,size,date}]}` | CALL `Agent.ListFiles`; parse Log lines | CALL |
| `compile` | `name, [new_symbol]` | `{ok, diagnostics:[…], symbol_file, code_bytes, data_bytes, key}` | CALL `ORP.Compile <name>[/s]`; parse Log | CALL |
| `load_module` | `name` | `{ok, res}` | CALL `Agent.Load`; parse Log/status | CALL |
| `unload_module` | `name` | `{ok}` / in_use | CALL `System.Free`; parse Log (` failed` ⇒ refcnt≠0) | CALL |
| `list_modules` | — | `{modules:[{name,refcnt,code_addr}]}` | CALL `Agent.ListModules`; parse Log | CALL |
| `run_command` | `cmd, [args]` | `{ok, res, log}` / trapped | raw CALL (escape hatch) | CALL |

**`compile` result — the one parser that earns its keep.** The proxy parses the CALL payload
(the compile Log delta). Real output, captured via `build-image` (§6):

```
  compiling Stars            <- "  compiling <Mod>"
  pos 59 undef               <- "  pos <N> <msg>"  (one per error, max 25)
  pos 69 illegal assignment
  pos 86 not Integer
compilation FAILED           <- failure trailer
```

Success instead ends the `compiling` line with the code/data sizes and key (and
` new symbol file` when a `.smb` was (re)written):

```
  compiling Stars new symbol file    45     8 C5386873
```

Parser contract:
- line `^  compiling (\w+)( new symbol file)?(\s+\d+\s+\d+\s+[0-9A-F]+)?` → module, `symbol_file`, and on success `code_bytes`/`data_bytes`/`key`.
- line `^  pos (\d+) (.*)$` → one diagnostic; `compilation FAILED` (or any `pos` line) ⇒ `ok=false`.
- `N` is a **byte offset** into the source. Map to `line:col` by counting line separators in
  the exact bytes PUT (CR and LF are each one byte → the count is line-ending-agnostic;
  validated in §6). Empirically the offset points **at or just past the offending token**
  (often the line-terminating newline), so it reliably identifies the **line**; the **column
  is approximate**. ⇒ each diagnostic returns `{line, col, msg, source_line, context}` where
  `context` is ±2 surrounding lines, giving the LLM enough to localize. The messages are
  terse (`undef`, `not Integer`, `illegal assignment`) but, with the source line, sufficient
  for iteration — that is the empirical claim §6 exists to test.

**CALL parameter channel.** Before `Oberon.Call`, the dispatcher must point `Oberon.Par` at
the received `par` bytes (commands read params via `Texts.OpenScanner(S, Oberon.Par.text,
Oberon.Par.pos)` — see `System.GetArg`). So: build a `Texts.Text` from `par`, then
`Oberon.SetPar(dummyFrame, T, 0)` — *not* `Oberon.Par.text := T` (Oberon-07 makes another
module's exported fields read-only to importers; the dummy `Display.Frame` is only used by
`SetPar` to derive `Par.vwr`, which our commands ignore). Consequences: params must be **inline** (the
`^`/selection path needs viewers and won't work headless); commands that read `Oberon.Par.vwr`
/`.frame` (e.g. `System.Open`, `System.Directory`) are out of scope for `run_command` — which
is why introspection gets its own Log-writing helpers (`Agent.*`).

### 4.4 `Agent.Mod` (concrete)

"PCLink1 grown up": the same byte I/O plus a `PUT`/`GET`/`CALL` dispatcher installed as an
`Oberon.Task`. It runs *inside* the live system, concurrent with the normal Oberon UI.
(Implemented in `oberon/Agent.Mod`; compiles clean to 747 B code / 252 B data and is validated
live — §7.)

**State (Phase 1 — the server is essentially stateless):**

```oberon
MODULE Agent;
  IMPORT SYSTEM, Kernel, Files, Modules, Texts, Oberon, FileDir;
  CONST data = -56; stat = -52; sync = 0A5H; rsync = 05AH;
    opPut = 1; opGet = 2; opCall = 3;
    stOk = 0; stNotFound = 1; stTrapped = 2; stError = 3;
  VAR
    T: Oberon.Task;        (* installed dispatcher *)
    W: Texts.Writer;       (* Log writes for the Agent.* helpers *)
    PW: Texts.Writer;      (* builds CALL par-text *)
    par: Texts.Text;       (* reused; holds the current CALL params *)
    name: ARRAY 32 OF CHAR;
```

There is **no chat/history state on the device in Phase 1** — the conversation lives on the
host (proxy). `Agent.Mod` is a request/response server. (Phase-2 on-device state: §4.7.)

**Key API (exported commands):**

| command | role |
|---|---|
| `Agent.Run*` | `Oberon.Install(T)` the dispatcher; install the trap handler (§5). Auto-run at boot. |
| `Agent.Stop*` | `Oberon.Remove(T)`. |
| `Agent.ListFiles*` | `FileDir.Enumerate(prefix, h)`; handler writes `name⟨tab⟩size⟨tab⟩date` per line to Log. |
| `Agent.ListModules*` | walk `Modules.root`, skip holes (`name[0]=0X`), write `name⟨tab⟩refcnt⟨tab⟩codeAdr`. |
| `Agent.Load*` | scan a name from `Oberon.Par`; `Modules.Load(name, m)`; log `loaded <name>` or `load failed res=<n>`. |

Internal (not commands): `Rec`/`Send`/`RecInt`/`SendInt`/`RecName`, `DoPut`/`DoGet`/`DoCall`,
`Task`, and (post-hardening) `Trap`/`ResetKeep`.

**Dispatcher shape:**

```oberon
PROCEDURE Task;  (* one poll tick; Oberon.Loop calls this when installed *)
  VAR op: BYTE;
BEGIN
  IF SYSTEM.BIT(stat, 0) THEN          (* a byte is waiting *)
    RecByteExpect(sync);               (* resync on mismatch *)
    Rec(op);
    IF    op = opPut  THEN DoPut
    ELSIF op = opGet  THEN DoGet
    ELSIF op = opCall THEN DoCall
    END
  END
END Task;
```

- `DoPut`: `RecName(name)`; `RecInt(len)`; `F := Files.New(name); Files.Set(R,F,0)`; loop
  `Rec(x); Files.WriteByte(R,x)` ×`len`; `Files.Register(F)`; reply `stOk`, empty.
- `DoGet`: `RecName(name)`; `F := Files.Old(name)`; if `NIL` reply `stNotFound`; else reply
  `stOk` + stream `Files.Length(F)` bytes via `Files.ReadByte`/`Send`.
- `DoCall`: `RecName(cmd)`; `RecInt(len)`; build `par` from `len` received bytes (`Texts.Write(PW,…)`
  → `Texts.Open(par,"")` → `Texts.Append(par, PW.buf)`); `Oberon.SetPar(parF, par, 0)`
  (dummy `Display.Frame`; `Par` fields are read-only to importers); `beg := Oberon.Log.len`;
  `Oberon.Call(cmd, res)`; map `res`→`status`
  (table §4.2); reply with the Log delta `[beg, Oberon.Log.len)`.

**Format & line-ending handling (proxy-side, so Oberon stays native):**
- **PUT (`write_file`)**: LLM sends LF text; proxy translates **LF→CR** and PUTs plain ASCII.
  Result is well-formed Oberon text (displays correctly in an Oberon viewer) whose first byte
  is ASCII, so `ORP` takes the ASCII path. (The compiler also tolerates LF as whitespace, but
  CR keeps files native.)
- **GET (`read_file`)**: if first byte is `0F1H` the file is formatted Texts → strip to the
  plain run (`ob2unix`); else raw ASCII. Translate **CR→LF** for the LLM.

**Self-modification gotcha (important for the north star).** `Modules.Load` returns the
*already-loaded* module if the name is present — it will **not** pick up freshly compiled
code. To reload a changed module: `unload_module` (`System.Free`, which fails if `refcnt≠0`)
**then** `load_module`. And a module cannot `Free` *itself* while it is the running task — so
`Agent.Mod` cannot hot-swap itself from inside; that needs a tiny external relay or a host
image-rebuild + reboot (§4.6). Phase 1 keeps `Agent.Mod` as stable infrastructure and has the
agent modify *other* modules.

### 4.5 Human interface (a human can drive the agent)

Three concurrent ways in, by design:

1. **Host operator console (primary, Phase 1).** The proxy exposes the *same* agent API to a
   human as to the LLM: an interactive REPL/TUI (`--interactive`) where a person types a task
   prompt or invokes tools directly, watches a live transcript (tool calls, tool results, and
   the raw Oberon Log), and can run in **step/approve mode** — each tool call (especially
   destructive ones: `delete_file`, `unload_module`, raw `run_command`) pauses for approval.
   This doubles as the §4.6 safety surface.
2. **The Oberon screen itself (free).** Because `Agent.Mod` is just an `Oberon.Task`, the
   normal Oberon UI keeps working: a human at the emulator window can open files, compile, and
   run commands *alongside* the agent, and read the shared `Oberon.Log`. Good for inspection
   and manual override.
3. **On-device prompt (Phase 2 direction).** A human types the task into an Oberon viewer/Tool
   text and a command reads it — the loop-in-Oberon "romance" (§4.7).

### 4.6 Languages, deployment, safety

- **Languages.** Oberon-07 on the device (in `oberon/`); **Python** proxy (in `python/`, a
  `uv` project; package `pucxy`). A future imaging/storage layer would be Rust or Zig, decided
  later, independent of the proxy.
- **Deployment.** `bin/build-image` compiles only a fixed module list, so
  `python/scripts/build_image.py` cross-compiles `Agent.Mod` via an unused leaf-module slot and
  bakes the resulting `Agent.rsc` into the image (`--no-precompile` ships source only). Bring-up
  is connect-mode: boot `bin/risc` with `--serial-in/out` on two FIFOs, run `Agent.Run` once in
  the Oberon window, then attach the proxy as a client. (Auto-start at boot would need a patched
  `Oberon.Mod` doing `Modules.Load("Agent")` — deferred, as is `ResetKeep` trap survival (§5);
  v1 has no trap handler.)
- **Safety.** Speculative execution on throwaway image copies; host supervisor (serial
  timeout → emulator reset) is the backstop for a hung or trapped server; `build-image` /
  Norebo is the clean-rebuild ground truth and rollback path; step/approve mode (§4.5) gates
  destructive tools.

**Dependencies (proxy).** Streaming output matters for a workable console (and lands squarely
in Phase 2), so the proxy takes a few thin deps rather than hand-rolling SSE + retry/backoff.

- *Runtime:* `openai` — LLM client; most providers expose an OpenAI-compatible API (Claude too,
  via its compatibility endpoint), kept behind a small **`llm_client` seam** so swapping to
  native `anthropic` (prompt caching / fine-grained streaming) is a one-file change. `rich` —
  host console: streaming transcript, code/diff highlighting, step/approve prompts. Optional
  `python-dotenv` for the API key (else env vars; non-secret config via stdlib `tomllib`).
  Providers in *thinking* mode (e.g. DeepSeek V4) stream a `reasoning_content` field that must
  be echoed back in the assistant message each turn — `llm.py` captures and replays it.
- *Tooling, `uv`-managed:* `uv` (project/deps), `ruff` (lint + format, replaces black), `ty`
  (types — Astral, still young; `pyright`/`mypy` the fallback), `pytest` (add `pytest-asyncio`
  only if the loop goes async).
- *Stdlib — no dep:* serial/PTY (`pty`, `termios`, `os`, `select`/`selectors`), wire framing
  (`struct`), emulator supervision (`subprocess` + watchdog), config (`tomllib`), logging.
- *Concurrency:* **sync** for Phase 1 — render a stream by iterating the response into
  `rich.Live`; go async only if the console must stay responsive mid-stream.

```toml
[project]
requires-python = ">=3.11"
dependencies = ["openai", "rich"]     # + "python-dotenv" if using .env

[dependency-groups]
dev = ["pytest", "ruff", "ty"]        # ruff format replaces black
```

### 4.7 On-device state (Phase 2 sketch, not Phase 1)

When the loop moves into Oberon, the conversation needs an on-device home. JSON still stays
host-side; the device stores the wire-level transcript. Likely model: an append-only list of
messages, each body a `Texts.Text`, persisted to a file for resume across reboots.

```oberon
TYPE Msg = POINTER TO MsgDesc;
  MsgDesc = RECORD role: INTEGER; (* user | assistant | tool *) body: Texts.Text; next: Msg END;
VAR history: Msg;   (* head; append on each turn; serialize to "Agent.Chat" *)
```

Deferred until Phase 1 works end-to-end.

## 5. Trap survival (the hard part)

A trapping `CALL` (a bad `LOAD` or a runtime fault) would hit the default `System.Trap` →
`Oberon.Reset`, which **removes the active task** — i.e. kills the serial server.

Fix:

- A patched **`Oberon.ResetKeep`** — `Oberon.Reset` minus the task removal. The only change is
  one line (`SP = R14`):
  ```oberon
  PROCEDURE Reset*;       (* original *)
  BEGIN IF CurTask.state = active THEN Remove(CurTask) END ;
    SYSTEM.LDREG(14, Kernel.stackOrg); Loop END Reset;
  PROCEDURE ResetKeep*;   (* patched: keep the task, just idle it *)
  BEGIN IF CurTask.state = active THEN CurTask.state := idle END ;
    SYSTEM.LDREG(14, Kernel.stackOrg); Loop END ResetKeep;
  ```
- A custom trap handler in `Agent.Mod`, installed via `Kernel.Install(SYSTEM.ADR(Trap), 20H)`
  (mirrors `System.Trap`), that: **preserves the `w = 0 → Kernel.New` allocation path** (the
  same handler services every `NEW`, so dropping it breaks all allocation); decodes the trap
  (`u := SYSTEM.REG(15); SYSTEM.GET(u-4, v); w := v DIV 10H MOD 10H`; `pos = v DIV 100H MOD
  10000H`; module by code-range `mod.code ≤ u < mod.imp`); appends the trap text to the Log;
  **sends the pending `trapped` response** (status 2 + the trap text as payload); then calls
  `ResetKeep`.

This reuses `Reset`'s proven SP-reset + re-enter-`Loop` maneuver minus the task removal — a
small variation, not a true longjmp, but it **needs validation on the emulator**.

**Empirical notes (from §6) that constrain the handler:**
- The *live* trap format is `System.Trap`'s `  pos <N>  TRAP<w> in <Mod> at <hexAddr>` — **not**
  Norebo's `shim: <msg> at <name> pos <n>`. The on-device handler reproduces the former; only
  Norebo's *decode prologue* (the `REG(15)`/`v` arithmetic) is copyable, not its output.
- A trapped `LOAD` may leave a zombie half-initialised module → expose cleanup/unload (the
  reload cycle in §4.4 applies). Backstop regardless: the host supervisor (serial timeout →
  emulator reset). Norebo doesn't help (it halts per-process).

## 6. Testing methodology

**We can elicit real compiler diagnostics today, with no emulator boot,** via `bin/build-image`
— the shim routes Oberon's Log to host stdout, so a build surfaces verbatim `ORP`/`ORS` output.
This is how the §4.3 `compile` parser is specified against *real* text rather than guesses.

- **Compile-diagnostic fixtures (the headline check the spec was waiting on).** Recipe:
  copy `op2013-src/` → a scratch tree, overwrite one **leaf** module that has no dependents
  in build-image's fixed list (`Stars`, `Hilbert`, `Sierpinski`, `Blink`, `Checkers`) with a
  deliberately broken body, run `build-image <scratch> /tmp/x.dsk`, capture stdout. Whole-image
  build ≈ 2 s. Caveats: build-image compiles a **fixed module list**, and `ORP.Compile` stops
  at the first module whose `errcnt ≠ 0`, so put the break in **one** early leaf module per
  fixture. Save captured logs as fixtures; the proxy's parser unit-tests run against them
  **without** the emulator.

  *Captured baseline (real output, do not hand-edit):*
  ```
    compiling Stars
    pos 59 undef
    pos 69 illegal assignment
    pos 86 not Integer
  compilation FAILED
  ```
  *Findings now baked into §4.3:* exact line shape is `\n  pos <N> <msg>`; `N` is a byte
  offset; **CR and LF give identical offsets**; the offset sits at/just past the offending
  token (reliable line, approximate column); messages are terse but localizable with the
  source line. Success line: `  compiling <Mod>[ new symbol file]  <code> <data> <keyHex>`;
  `/s` forces a new symbol file (`ORP.Option` → `newSF`).

- **Proxy unit tests (no emulator):** frame encode/decode incl. the `0A5H`/`05AH` sync and LE
  ints; `edit_file` str_replace (unique-match + not-found + not-unique); compile-Log parsing
  against the fixtures above; char-offset → line:col mapping (assert line is exact); CR↔LF and
  `0F1H`-header strip round-trips. Pure Python, fast.

- **Oberon compile checks:** compile `oberon/Agent.Mod` and any patched system modules
  (`Oberon.Mod` with `ResetKeep`) via `build-image` as ground truth — a green image build is
  the gate that the device-side code is well-formed.

- **Integration:** boot the custom image + proxy over a PTY (or two FIFOs); exercise the wire
  protocol directly (`PUT`/`GET`/`CALL`) with byte-level assertions, including the `res`→`status`
  mapping.

- **Determinism:** prefer `risc headless --frames N [--hash]` and throwaway image copies for
  repeatable runs.

## 7. Verify method (does it actually work?)

**Validated 2026-05-29** — live PO2013 image + proxy over a serial FIFO, driven by
`deepseek-v4-pro`: the golden path (write → compile clean → `load_module` → `run_command`, log
captured), a real authoring task, and **error-channel recovery** (a planted two-error module —
the agent localized from `{msg, line, source_line, context}`, fixed both, and even backed out of
its own wrong `COPY` guess via the follow-up `undef`, reaching a clean compile). ⇒ the terse
`ORS` diagnostics are sufficient for LLM iteration; the approximate column never mattered. Still
open: trap survival (§5) and the throwaway-image regression run.

- **Golden-path smoke test:** PUT a small module → `compile` a deliberately-broken version →
  assert structured `diagnostics` (line exact, message present, `source_line` echoed) → fix →
  `compile` clean → `load_module` → `run_command` → read Log output.
- **Error-channel sufficiency (answered — see above):** drive a real fix-loop on the
  §6 fixtures and confirm an LLM can localize from `{msg, line, source_line, context}` despite
  terse messages and approximate columns. If not, widen `context` or add a source-line caret
  at `col` — *do not* try to improve `ORS`'s messages.
- **Trap survival (post-hardening):** load a module whose body traps; confirm the server
  **survives**, returns a `trapped` (status 2) result with the `TRAP w … at addr` text, and the
  system stays up; then confirm the half-loaded module can be cleaned up and reloaded.
- **Real-task drive:** run the actual agent loop on a small genuine task (author a module,
  compile, load, run) end-to-end — not just green unit tests.
- **Regression:** re-run smoke + unit suites against a throwaway image copy.

## 8. Open decisions

Reasonable calls made above, flagged so they're easy to revisit:
- **Sync bytes** `0A5H`/`05AH` in every frame (cheap desync detection + version tag). Drop if
  framing proves robust without them.
- **Canonical line endings:** LF on the host/LLM side, CR on the device (PUT translates
  LF→CR, GET translates CR→LF). Alternative: keep LF everywhere (the compiler tolerates it),
  at the cost of files that display wrong in Oberon viewers.
- **`status` granularity:** compressed `res`→4 codes (§4.2). If the proxy needs the exact
  `res`, prepend a `res:1` byte to CALL payloads instead of widening `status`.
- **Phase-2 chat store** (§4.7) — deferred until Phase 1 closes.
