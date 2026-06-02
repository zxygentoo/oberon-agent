# AGENTS.md — working notes for agents & contributors

Conventions and preferences for anyone (human or AI) working in this repo. `CLAUDE.md`
symlinks here.

**Read first:** [`README.md`](README.md) for the overview, and [`spec.md`](spec.md) for the
authoritative design, research findings, and locked decisions.

## How we work here

- **No hidden memory.** Keep durable project knowledge in the repo's visible docs
  (`README.md`, `spec.md`, this file) — not in any assistant-private memory store. If you
  learn something worth keeping, write it down here or in `spec.md`.
- **Spec before code.** When a design discussion converges, capture it in `spec.md` and
  align before implementing. Docs are living — iterate on them.
- **Design tools for the agent, not for Oberon.** The agent API (proxy ↔ LLM) should be
  explicit, named tools with structured results; the wire protocol (Oberon ↔ proxy) stays
  minimal (`PUT`/`GET`/`CALL`). The proxy bridges them. Collapsing everything to one
  generic verb is explicitly *not* a goal — favor clarity for the LLM that consumes the tools.

## Conventions

- Oberon side: Extended Oberon (Oberon-2 2020 Edition — a superset of Oberon-07 adding
  type-bound procedures, FINAL blocks, and safe module unloading). Our additions live in
  `oberon/`: new modules as `<Name>.Mod` (LF text — currently `Puck.Mod`), modifications
  to upstream modules as `<Name>.Mod.patch` (unified diffs against the EO source —
  currently `Oberon.Mod.patch`, which adds the boot-time `Modules.Load("Puck", ...)`).
  Host proxy: Python, in `python/` (a `uv` project; package `puck`).
- Vendored upstream (git submodules, in `vendor/`):
  - `vendor/risc-emu/` — Rust port of the RISC5 emulator + host tools (`risc`,
    `build-eo-image`, `extract-source`, `ob2txt`/`txt2ob`, …).
  - `vendor/extended-oberon/` — Andreas Pirklbauer's Extended Oberon distribution;
    `Documentation/S3RISCinstall.tar.gz` is the stock disk image we extract source from.
- Build outputs (gitignored, under `build/`): `eo-stock.dsk` (the stock EO image),
  `eo/` (source extracted from it), `src/` (assembled tree = `eo/.` + patches applied +
  our `*.Mod` dropped in), `puck.dsk` (the final bootable image), and `wip/` (the
  editable patched-upstream tree used by `make patches`).
- Session logs (gitignored): `log/`.

## Build

The toplevel `Makefile` drives everything; from a fresh `git clone --recurse-submodules`,
`make image` walks the full chain (`tools` → `eo-source` → `image`).

- `make tools` — `cargo build --release --workspace --bins` inside `vendor/risc-emu/`.
- `make eo-source` — untar `Documentation/S3RISCinstall.tar.gz`, run `extract-source`
  on `RISC.img` → `build/eo/`. Idempotent (stamp file).
- `make image` — assemble `build/src/` (copy `build/eo/.`, apply `oberon/*.patch` via
  `ob2txt`/`patch`/`txt2ob` roundtrip, drop in `oberon/*.Mod` converted to CR), then
  `build-eo-image build/src build/puck.dsk`.
- `make oberon` / `make puck` — run the emulator / puck against `/tmp/p.in`+`/tmp/p.out`
  (override with `FIFO_IN=` / `FIFO_OUT=`); both tee a timestamped log into `log/`. The
  proxy requires `--model` and `--api-key`; forward them (and any other puck flags) via
  `ARGS=`, e.g. `make puck ARGS="--model=deepseek --api-key=$KEY"`.
- `make clean` — `rm -rf build`.   `make distclean` — also wipes the cargo target dir.

### Editing an upstream module

```
make wip            # populates build/wip/ = build/eo/. with patches applied
$EDITOR build/wip/Oberon.Mod
make patches        # regenerates oberon/Oberon.Mod.patch (only for files that differ)
make image          # rebuild with the new patch
```

Patches are stored as **LF unified diffs** (readable in git, easy to review). The build
roundtrips through `ob2txt`/`txt2ob` so the on-disk module stays in EO's CR format.

## Style preferences

- Prefer short, focused functions. If a block of logic has a clear purpose, extract it — even if it's only called once. Give it a good name and place it right below the caller. Single-use helpers are fine.
- Strongly prefer functional style. Use classes only when they genuinely manage state at the outer boundary of the system. Dataclasses for structured data are fine, custom Exceptions are fine. Default to plain functions + modules.
- Python 3.12: native `X | None`, `list[X]`, `collections.abc.Iterator`. No `from __future__ import annotations`.
- LBYL over EAFP where practical.
- **Avoid lazy imports if a top-level import works.** Function-scoped `import` is fine *during* incremental editing for speed (don't break flow to hoist on every edit), but post-check at natural pauses — at the end of a feature, before pushing, and especially during refactor passes — and hoist anything that doesn't actually need to be lazy. Reserve in-function imports for the cases that do require them: import-time cycles, optional/heavy deps, or platform-conditional imports.
