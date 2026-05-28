# puck

A coding agent that runs on — and modifies — a *live* Project Oberon (2013) system.

Oberon can compile and load/unload modules in the running system, so an agent with the
right tools can change the live system from the inside (and, as a stretch goal, the
compiler toolchain itself). puck is a thin host-side proxy that bridges Oberon's serial
line to an LLM, plus a small Oberon-side server that exposes the system as agent tools.

**Status:** early — design phase, no runnable code yet. See [`spec.md`](spec.md) for the
design and decisions, and [`AGENTS.md`](AGENTS.md) for working notes.

## How it fits together

```
LLM  <-HTTPS/JSON->  host proxy (Python)  <-RS232 wire protocol->  Agent.Mod on Oberon
```

The serial line is the only channel out of the emulated machine. The proxy owns HTTPS and
JSON; the Oberon side stays a small, dumb server. Phase 1 runs the agent loop on the host;
Phase 2 (stretch) moves it into Oberon.

## Repo layout

- `oberon/` — our Oberon-07 source (`Agent.Mod` and any patched system modules).
- `python/` — the Python host proxy: its own `uv` project (`src/pucxy/`, `tests/`, `pyproject.toml`).
- `README.md`, `AGENTS.md` — committed docs (`CLAUDE.md` symlinks to `AGENTS.md`).
- `spec.md` — the authoritative design doc (research findings, decisions, phasing).
- `op2013-src/` — Project Oberon 2013 reference source (gitignored).
- `book/` — Project Oberon book PDFs (gitignored).
- `bin/` — built tools: the `risc` emulator and `build-image` (gitignored).

## Dependencies / context

- **PO2013 reference** — `op2013-src/` (ORP/ORG/ORB/ORS compiler, Modules, Files, Texts,
  PCLink1, RS232, …) and `book/` (the Project Oberon book: PO.System, PO.Computer,
  PO.Applications, Oberon07.Report, UsingOberon, PIO).
- **Emulator & tools** — `bin/risc` (the RISC5 emulator) and `bin/build-image` (headless
  compiler / image builder), built from the sibling repo `../oberon-risc-emu-rs`. Raw
  serial via `--serial-in`/`--serial-out` (Unix); `--mem` for RAM. The wireless network is
  not emulated.

> `op2013-src/`, `book/`, and `bin/` are reference / vendored / built material — gitignored
> and populated locally.

## License

MIT — see [`LICENSE`](LICENSE).
