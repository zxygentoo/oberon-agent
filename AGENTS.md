# AGENTS.md — working notes for agents & contributors

Conventions and preferences for anyone (human or AI) working in this repo. `CLAUDE.md`
symlinks here.

**Read first:** [`README.md`](README.md) for the overview, and
[`skill/oberon-agent/SKILL.md`](skill/oberon-agent/SKILL.md) for the agent-side
working rules.

## How we work here

- **No hidden memory.** Keep durable project knowledge in the repo's visible docs
  (`README.md`, `skill/oberon-agent/SKILL.md`, this file) — not in any assistant-private
  memory store. If you learn something worth keeping, write it down here.
- **Design tools for the agent.** The CLI surface (`oat <subcommand>`) is explicit,
  named tools with structured results; the wire protocol (Oberon ↔ host) stays minimal
  (`PUT`/`GET`/`CALL`/`EDIT`). The CLI bridges them. Collapsing everything to one
  generic verb is explicitly *not* a goal — favor clarity for the LLM that drives the
  tools. Minimal ≠ frozen: `EDIT` earned its opcode (atomic on-device replace, no
  full-file round-trip) rather than being smuggled through `CALL` par-text.

## Conventions

- **Oberon side.** One shared protocol module plus a per-variant command module
  under `Mod/`. The split is semantic, not textual: `AgentProtocol` owns the wire,
  `AgentTool` owns the commands — duplication between the two variants is accepted;
  the goal is per-file readability, not zero duplication.
  - `Mod/Common/AgentProtocol.Mod` — the wire protocol, consumed verbatim by both
    images: serial byte layer (private), frame parsing, the `PUT`/`GET`/`EDIT`
    handlers, CALL framing, and the poll task. Its whole interface is `Executor`
    (run a parsed command, return a status), `Start(exec)`, and the three status
    consts. The variant never touches a serial byte.
  - `Mod/ExtendedOberon/AgentTool.Mod` — for Extended Oberon (Oberon-2 2020 Edition,
    Oberon-07 plus type-bound procedures, FINAL blocks, and safe module unloading).
  - `Mod/ProjectOberon/AgentTool.Mod` — for Wirth's Project Oberon 2013 (plain
    Oberon-07, no FINAL, no safe-unload).
  - Each variant `AgentTool.Mod` = its `Exec` (`Oberon.SetPar` arity, `Oberon.Call`
    vs `Modules.Call`, res mapping) + the named commands, which must live in the
    module called `AgentTool` because oat invokes them by that name. Import-order
    note: ORB requires modules referenced by an interface to be imported before the
    module using them, so `AgentProtocol` (whose interface references `Texts`) is
    imported last.
  - Modifications to upstream modules live alongside each variant as
    `<Name>.Mod.patch` (unified LF-text diffs against the extracted source).
  - `AgentTool.Mod` exports: `ListFiles`, `ListModules`, `Load`, and `Version`
    (reports the variant to the Log; the `oat check` smoke-test parses it). EO echoes
    the stock `System.Version`; PO hardcodes its string — PO2013 symbol files don't
    carry string-constant characters, so an imported CONST reads back empty.
  - The `EDIT` opcode matches OLD inside a fixed device buffer: `editLim` in
    `AgentProtocol.Mod` must equal `EDIT_OLD_LIMIT` in `oat/src/protocol.rs` (1024).
    Longer OLDs transparently fall back to the host-side GET+PUT path in tools.rs.
    Protocol changes need image and `oat` rebuilt from the same tree — an old image
    silently ignores unknown opcodes and the host times out.
  - Trap survival: `Oberon.Reset` removes the *active* task when a handler traps,
    which used to kill the wire. `AgentProtocol` runs a one-line watchdog task
    (`Oberon.Install` is idempotent) that reinstalls the serial task, and an
    in-flight flag lets the revived task finish the interrupted exchange with
    `stTrapped` + the Log delta (which carries the `TRAP` line). No upstream
    patch. Residual: a trap during response *transmission* still costs that one
    exchange — the stateless CLI recovers on the next invocation.
- **Host CLI.** Rust binary `oat` in `oat/` — single Cargo crate (package = `oat`,
  binary = `oat`). Deps: `clap` (4, derive), `rustix` (safe wrappers for the
  POSIX syscalls std doesn't cover). The crate `forbid`s unsafe. Layering:
  `protocol.rs` is the shared vocabulary (frame codec, `Response`, the `Status`
  enum — wire bytes stay private to the codec — and the `Request` seam);
  `transport.rs` implements `Request` over a PTY/FIFO pair; `tools.rs` codes
  against `Request` and turns statuses into typed `Error`s; `cli.rs` is the
  whole CLI surface and the composition root (clap → construct `Transport` →
  call `tools`), exporting exactly one function, `run()`; `main.rs` is the
  process contract (run, prefix errors, map exit codes). `tools` and
  `transport` depend only on `protocol`, never on each other; `cli` and `main`
  never touch `protocol`.
- **Skill.** `skill/oberon-agent/SKILL.md` — one file, covers both variants. Branches
  on `oat check`'s reported `System.Version` string. PO-specific: warns + asks for
  operator permission before any `unload` (PO has no safe-unload).
- **Upstream pieces:**
  - `vendor/oberon-risc-emu-rs/` (git submodule) — Rust port of the RISC5 emulator +
    host tools (`risc`, `build-po-image`, `build-eo-image`, `extract-source`,
    `ob2txt`/`txt2ob`). Carries the stock PO disk image at
    `DiskImage/Oberon-2020-08-18.dsk`.
  - **EO stock image** — downloaded by `make` to `build/S3RISCinstall.tar.gz` from
    upstream (`andreaspirklbauer/Oberon-extended`), not vendored. The full EO repo
    is ~12 MB and we only consume this single file.
- **Build outputs** (gitignored): `build/` (extracted source per variant + assembled
  trees), `DiskImage/` (`ProjectOberon.dsk`, `ExtendedOberon.dsk`), `oat/target/`.

## Build

Top-level `Makefile` drives everything; from a fresh `git clone --recurse-submodules`:

- `make eo-image` → `DiskImage/ExtendedOberon.dsk`. Chain: `tools` → `eo-source` →
  apply patches + drop in `AgentTool.Mod` → `build-eo-image`.
- `make po-image` → `DiskImage/ProjectOberon.dsk`. Same shape, source from
  `vendor/oberon-risc-emu-rs/DiskImage/Oberon-2020-08-18.dsk`, uses `build-po-image`.
- `make image` → both.
- `make eo-emu` / `make po-emu` → boot the corresponding emulator image on
  `/tmp/p.in`+`/tmp/p.out` (override with `FIFO_IN=` / `FIFO_OUT=`).
- `make tools` → the vendored emulator + host tools (`cargo build --release
  --workspace --bins` inside `vendor/oberon-risc-emu-rs/`) and the `oat` binary.
- `make test` → `test-unit` (cargo) + `test-po` + `test-eo`. The latter two run
  `test/integration.sh`: boot the image in the emulator (`--headless`, no display
  needed) on a private FIFO pair and drive the whole oat surface live (write/read/
  edit incl. error statuses and the >1 KiB fallback, compile/call/list/delete,
  EO hot-swap).
- `make clean` → `rm -rf build DiskImage`. `make distclean` → also wipes both cargo
  target dirs.

The CLI is also a normal Cargo crate — `cargo build --release` inside `oat/`, or
`cargo install --path oat` to put `oat` on PATH.

### Editing an upstream module

Patch workflow: extract → edit → diff against the original. The Makefile no longer
ships a `wip` / `patches` helper for the two-variant layout; do it by hand:

```
$(BIN)/ob2txt build/eo/Oberon.Mod              # converts to LF .txt sibling
cp build/eo/Oberon.Mod.txt /tmp/orig
$EDITOR build/eo/Oberon.Mod.txt
diff -u --label Oberon.Mod --label Oberon.Mod /tmp/orig build/eo/Oberon.Mod.txt \
  > Mod/ExtendedOberon/Oberon.Mod.patch
make eo-image                                  # rebuild with the new patch
```

Patches are stored as **LF unified diffs**. The build roundtrips through `ob2txt`
/`txt2ob` so the on-disk module stays in Oberon's CR format.

## Style preferences

### Oberon

- **UI commands open a Viewer with a system menu** (e.g. via
  `MenuViewers.New(menuF, mainF, …)`), not a headless command that writes to
  `Oberon.Log`. Reserve plain `Oberon.Log` writers for non-interactive use —
  automation, protocol handlers (e.g. `AgentProtocol.Task`), headless introspection.
- Match the variant's idioms: type-bound procedures and FINAL blocks on EO only;
  PO modules wanting clean tear-down need explicit `Close*` commands.

### Rust (`oat`)

- Prefer short, focused functions. If a block of logic has a clear purpose, extract
  it — even if it's only called once. Single-use helpers are fine.
- `std::io::Read`/`Write` traits, `&File` impls — and `rustix` (never raw `libc`)
  for what `std` genuinely doesn't cover (`poll`, termios raw mode). The crate
  has `#![forbid(unsafe_code)]`; keep it that way.
- Strong types over stringly-typed flags. Specific `Error` variants over a single
  `Error::Other(String)`.
- Clippy-pedantic clean is the bar.
