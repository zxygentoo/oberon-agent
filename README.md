# puck

A coding agent that runs on — and modifies — a *live* Extended Oberon system.

## The idea

Extended Oberon (a 2020-era superset of Niklaus Wirth's Oberon-07) is unusual in that
modules can be compiled, loaded, and *safely unloaded* in the running system. With the
right tools, an agent can change the live system from the inside — edit a module, recompile,
unload the old code, load the new code, and keep going without rebooting. As a stretch goal,
that includes the compiler toolchain itself.

puck wires this up:

- An **LLM** drives the agent loop via a named tool API.
- A **Python host proxy** (`puck`) owns HTTPS/JSON, the tool implementations, and a serial
  link to the emulated machine.
- A small **Oberon-side server** (`Puck.Mod`) speaks a three-op wire protocol — `PUT` / `GET`
  / `CALL` — and lets the proxy read files, write files, and run any `Mod.Proc` command
  inside the live system.

```
LLM  <-HTTPS/JSON->  host proxy (Python)  <-RS232 wire protocol->  Puck.Mod on Oberon
```

The agent loop runs on the host. The Oberon side stays a small, dumb request/response
server. The proxy bridges them, exposing the wire ops as explicit, named tools to the LLM:
read/write/edit/delete files, list files and modules, compile, load and (safely) unload
modules, plus a `run_command` escape hatch.

See [`spec.md`](spec.md) for the design and [`AGENTS.md`](AGENTS.md) for working notes.

## How the vendored pieces are used

Two git submodules under `vendor/` supply everything we don't write ourselves.

**[`vendor/risc-emu/`](https://github.com/zxygentoo/oberon-risc-emu-rs)** — a Rust port of Wirth's RISC5 emulator and host tools:

- `risc` — runs the emulator (the GUI window where Oberon boots).
- `extract-source` — pulls Oberon source out of a stock disk image into a host directory.
- `build-eo-image` — compiles a source tree (topologically, via a host-side shim) and
  produces a bootable `.dsk`. Used to bake `Puck.Mod` into the image.
- `ob2txt` / `txt2ob` — convert between Oberon's CR-terminated module format and plain LF
  text, so patches stay readable in git.

**[`vendor/extended-oberon/`](https://github.com/andreaspirklbauer/Oberon-extended)** — Andreas Pirklbauer's Extended Oberon distribution:

- `Documentation/S3RISCinstall.tar.gz` ships a stock EO disk image.
- `make eo-source` untars it and runs `extract-source` to populate `build/eo/` with the
  upstream source tree.
- We add our modules and apply our patches on top of `build/eo/.` to produce the final
  `build/puck.dsk`.

## Quick start

### 1. Prereqs

- Rust toolchain (`cargo`) — for the emulator + host tools.
- [`uv`](https://docs.astral.sh/uv/) — for the Python proxy.
- Standard Unix tools: `make`, `tar`, `patch`.
- `rlwrap` (optional) — readline in the agent REPL if present.

### 2. Clone with submodules

```
git clone --recurse-submodules https://github.com/zxygentoo/puck.git
cd puck
```

If you forgot `--recurse-submodules`, run:

```
git submodule update --init --recursive
```

### 3. Build the disk image

```
make image
```

This walks the chain `tools` → `eo-source` → `image`: builds the Rust tools, extracts the
EO source from the stock tarball, applies our patches, drops in `Puck.Mod`, and produces
`build/puck.dsk`. Cold build ~3–5 min; warm rebuilds ~5 s.

### 4. Create the serial FIFOs (once)

The host proxy and the emulator talk over two named pipes:

```
mkfifo /tmp/p.in /tmp/p.out
```

### 5. Run the emulator

```
make oberon
```

This opens the Oberon GUI window. `Puck.Mod` auto-starts at boot (our patched `Oberon.Mod`
loads it after `System`), so the wire server is already listening on the serial line.

### 6. Run the agent

In a second shell — `--model` (provider) and `--api-key` are required; pass them via
`ARGS=`:

```
make puck ARGS="--model=deepseek --api-key=<your-key>"
```

Supported providers: `deepseek`, `openai`, `claude` (each ships with a sensible base
URL + default model). You get a REPL: type a task, watch the proxy drive `puck`'s tools
against the running emulator, and see the live Oberon Log streamed back.

For the full list of proxy flags (FIFO or PTY overrides, one-shot TASK, log path, …),
run:

```
cd python && uv run puck --help
```

## License

MIT — see [`LICENSE`](LICENSE).
