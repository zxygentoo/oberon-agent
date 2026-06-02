# puck

A coding agent that runs on — and modifies — a *live* Extended Oberon system.

Extended Oberon can compile, load, and *safely unload* modules in the running system, so
an agent with the right tools can change the live system from the inside (and, as a
stretch goal, the compiler toolchain itself). The host-side proxy runs the agent loop and
talks to the LLM; a small Oberon-side server (`Puck.Mod`) handles the wire protocol on
the device.

**Status:** Working — `puck` (the host proxy) drives a live Extended Oberon image
through a set of named tools: read/write/edit/delete files, list files and modules,
compile, load and (safely) unload modules, plus a `run_command` escape hatch. See
[`spec.md`](spec.md) for the design and [`AGENTS.md`](AGENTS.md) for working notes.

## How it fits together

```
LLM  <-HTTPS/JSON->  host proxy (Python)  <-RS232 wire protocol->  Puck.Mod on Oberon
```

The serial line is the only channel out of the emulated machine. The host proxy owns
HTTPS and JSON; the Oberon side stays a small, dumb server speaking three wire ops
(`PUT`/`GET`/`CALL`). The agent loop runs on the host, exposing the wire ops as
explicit, named tools to the LLM.

## Quick start

Prereqs: a Rust toolchain (`cargo`) for the emulator + host tools, [`uv`](https://docs.astral.sh/uv/)
for the Python proxy, and standard Unix tools (`make`, `tar`, `patch`). `rlwrap` is used by
`make agent` if present (optional — readline in the REPL).

```
git clone --recurse-submodules https://github.com/zxygentoo/puck.git
cd puck
# if cloned without --recurse-submodules, run:
#   git submodule update --init --recursive
make image                                    # builds tools + extracts EO + puck.dsk

mkfifo /tmp/p.in /tmp/p.out                   # once
make oberon                                   # runs the emulator (GUI)

# in another shell:
export LLM_API_KEY=...
make agent                                    # drives puck against the running emu
```

## Repo layout

- `oberon/` — our additions to Extended Oberon. New modules live as `<Name>.Mod`
  (currently `Puck.Mod` — the wire endpoint); modifications to upstream modules live as
  `<Name>.Mod.patch` unified diffs against EO (currently `Oberon.Mod.patch` — loads `Puck`
  at boot).
- `python/` — the Python host proxy: its own `uv` project (`src/puck/`, `tests/`, `pyproject.toml`).
- `Makefile` — `make image` (build the disk), `make oberon` (run the emulator),
  `make agent` (drive puck against the running emulator); plus `tools`, `eo-source`,
  `wip`, `patches`, `clean`, `distclean` — see the file or `AGENTS.md`.
- `vendor/risc-emu/` — submodule: Rust port of the RISC5 emulator + host tools.
- `vendor/extended-oberon/` — submodule: Andreas Pirklbauer's Extended Oberon
  distribution (we extract source from `Documentation/S3RISCinstall.tar.gz`).
- `README.md`, `AGENTS.md` — committed docs (`CLAUDE.md` symlinks to `AGENTS.md`).
- `spec.md` — the authoritative design doc (research findings, decisions, system design).
- `build/` — generated tree (gitignored): `eo-stock.dsk`, `eo/`, `src/`, `wip/`, `puck.dsk`.
- `log/` — session logs (emulator + puck), gitignored.

## License

MIT — see [`LICENSE`](LICENSE).
