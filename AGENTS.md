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
  (`PUT`/`GET`/`CALL`). The CLI bridges them. Collapsing everything to one generic verb
  is explicitly *not* a goal — favor clarity for the LLM that drives the tools.

## Conventions

- **Oberon side.** Two variants, parallel layout under `Mod/`:
  - `Mod/ExtendedOberon/AgentTool.Mod` — for Extended Oberon (Oberon-2 2020 Edition,
    Oberon-07 plus type-bound procedures, FINAL blocks, and safe module unloading).
  - `Mod/ProjectOberon/AgentTool.Mod` — for Wirth's Project Oberon 2013 (plain
    Oberon-07, no FINAL, no safe-unload).
  - Modifications to upstream modules live alongside each variant as
    `<Name>.Mod.patch` (unified LF-text diffs against the extracted source).
  - `AgentTool.Mod` exports: `ListFiles`, `ListModules`, `Load`, and `Version`
    (echoes `System.Version` to the Log; the `oat check` smoke-test parses it).
- **Host CLI.** Rust binary `oat` in `oat/` — single Cargo crate (package = `oat`,
  binary = `oat`). Deps: `clap` (4, derive), `libc` (POSIX I/O).
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
  automation, protocol handlers (e.g. `AgentTool.Task`), headless introspection.
- Match the variant's idioms: type-bound procedures and FINAL blocks on EO only;
  PO modules wanting clean tear-down need explicit `Close*` commands.

### Rust (`oat`)

- Prefer short, focused functions. If a block of logic has a clear purpose, extract
  it — even if it's only called once. Single-use helpers are fine.
- `std::io::Read`/`Write` traits, `&File` impls — avoid raw `libc::read`/`write`
  except where `std` genuinely doesn't cover it (`poll`, `tcsetattr`).
- Strong types over stringly-typed flags. Specific `Error` variants over a single
  `Error::Other(String)`.
- Clippy-pedantic clean is the bar.
