# puck

A coding agent that runs on — and modifies — a *live* Extended Oberon system.

Extended Oberon can compile, load, and *safely unload* modules in the running system, so
an agent with the right tools can change the live system from the inside (and, as a
stretch goal, the compiler toolchain itself). puck is a thin host-side proxy that bridges
Oberon's serial line to an LLM, plus a small Oberon-side server that exposes the system
as agent tools.

**Status:** Phase 1 working — the host-side agent loop drives a live Extended Oberon
image over a FIFO-attached serial line. See [`spec.md`](spec.md) for the design and
decisions, and [`AGENTS.md`](AGENTS.md) for working notes.

## How it fits together

```
LLM  <-HTTPS/JSON->  host proxy (Python)  <-RS232 wire protocol->  Agent.Mod on Oberon
```

The serial line is the only channel out of the emulated machine. The proxy owns HTTPS and
JSON; the Oberon side stays a small, dumb server. Phase 1 runs the agent loop on the host;
Phase 2 (stretch) moves it into Oberon.

## Quick start

```
git clone --recurse-submodules https://github.com/zxygentoo/puck.git
cd puck
make image                                    # builds tools + extracts EO + puck.dsk

mkfifo /tmp/p.in /tmp/p.out                   # once
make oberon                                   # runs the emulator (GUI)

# in another shell:
export PUCXY_API_KEY=...
make agent                                    # drives pucxy against the running emu
```

## Repo layout

- `oberon/` — our additions to Extended Oberon. New modules live as `<Name>.Mod`
  (currently `Agent.Mod` — the wire endpoint); modifications to upstream modules live as
  `<Name>.Mod.patch` unified diffs against EO (currently `Oberon.Mod.patch` — loads Agent
  at boot).
- `python/` — the Python host proxy: its own `uv` project (`src/pucxy/`, `tests/`, `pyproject.toml`).
- `Makefile` — `make image` (build the disk), `make oberon` (run the emulator),
  `make agent` (drive pucxy against the running emulator); plus `tools`, `eo-source`,
  `wip`, `patches`, `clean`, `distclean` — see the file or `AGENTS.md`.
- `vendor/risc-emu/` — submodule: Rust port of the RISC5 emulator + host tools.
- `vendor/extended-oberon/` — submodule: Andreas Pirklbauer's Extended Oberon
  distribution (we extract source from `Documentation/S3RISCinstall.tar.gz`).
- `README.md`, `AGENTS.md` — committed docs (`CLAUDE.md` symlinks to `AGENTS.md`).
- `spec.md` — the authoritative design doc (research findings, decisions, phasing).
- `build/` — generated tree (gitignored): `eo-stock.dsk`, `eo/`, `src/`, `wip/`, `puck.dsk`.
- `log/` — session logs (emulator + pucxy), gitignored.

## License

MIT — see [`LICENSE`](LICENSE).
