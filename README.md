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

## Repo layout

- `oberon/` — our Oberon-2 / EO source: `Agent.Mod` (the wire endpoint) and a patched
  `Oberon.Mod` (loads Agent at boot); both override their EO upstream counterparts when
  the image is built.
- `python/` — the Python host proxy: its own `uv` project (`src/pucxy/`, `tests/`, `pyproject.toml`).
- `Makefile` — `make image` (build the disk), `make oberon` (run the emulator),
  `make agent` (drive pucxy against the running emulator).
- `README.md`, `AGENTS.md` — committed docs (`CLAUDE.md` symlinks to `AGENTS.md`).
- `spec.md` — the authoritative design doc (research findings, decisions, phasing).
- `eo/` — Extended Oberon source (gitignored).
- `po2013/` — Project Oberon 2013 reference source (gitignored).
- `book/` — Project Oberon book PDFs (gitignored).
- `bin/` — host tools: `risc` (RISC5 emulator), `build-eo-image` / `build-image`,
  `extract-source`, `ob2unix` / `ob2txt` / `txt2ob` (gitignored).
- `build/`, `log/` — local build outputs and session logs (gitignored).

## Dependencies / context

- **Extended Oberon source** — `eo/` (the running system: ORP/ORG/ORB/ORS compiler,
  Modules with safe-unload, Files, Texts, Viewers, …). Built on top of PO2013.
- **PO2013 reference** — `po2013/` (the original sources) and `book/` (the Project Oberon
  book: PO.System, PO.Computer, PO.Applications, Oberon07.Report, UsingOberon, PIO).
- **Emulator & tools** — `bin/risc` (the RISC5 emulator) and `bin/build-eo-image`
  (headless EO compiler / image builder), built from the sibling repo
  `../oberon-risc-emu-rs`. Raw serial via `--serial-in`/`--serial-out` (Unix); `--mem`
  for RAM. The wireless network is not emulated.

> `eo/`, `po2013/`, `book/`, `bin/`, `build/`, and `log/` are reference / vendored /
> built / generated material — gitignored and populated locally.

## License

MIT — see [`LICENSE`](LICENSE).
