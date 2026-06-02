# pucxy

Host-side proxy for the **puck** coding agent on Extended Oberon (Oberon-2 2020 Edition,
derived from Project Oberon 2013).

It owns HTTPS + JSON to the LLM and speaks a minimal `PUT`/`GET`/`CALL` wire
protocol over Oberon's serial line to `Agent.Mod`. See the top-level
[`spec.md`](../spec.md) for the design.

```
uv sync
uv run pytest          # unit tests (no emulator needed)
uv run pucxy --help
```

Layout: `src/pucxy/` (package), `tests/` (unit tests). The image is built from the repo
root with `make image` (see below).

## Bring-up (Phase 1)

The emulator is a long-lived GUI process; the proxy is a client that connects to its
serial line. Run them **separately** — boot once (Agent auto-starts), then drive as
often as you like (no reboot per task).

1. Build an image with `Agent` compiled in — from the repo root:

   ```
   make image                                # -> build/puck.dsk
   ```

   First time only, this also `cargo build`s the emulator + host tools in
   `vendor/risc-emu/` and extracts Extended Oberon source from
   `vendor/extended-oberon/Documentation/S3RISCinstall.tar.gz` into `build/eo/`. The
   image build then assembles `build/src/` (= `build/eo/.` + `oberon/*.patch` applied +
   `oberon/*.Mod` dropped in) and hands it to `build-eo-image`. See `AGENTS.md` for the
   patch edit cycle (`make wip` / `make patches`).

2. Boot the emulator on two FIFOs (from the repo root):

   ```
   mkfifo /tmp/p.in /tmp/p.out                 # once
   make oberon                                 # backgrounds the emulator + tees log/
   ```

   Or invoke the binary directly:
   ```
   ./vendor/risc-emu/target/release/risc --serial-in /tmp/p.in --serial-out /tmp/p.out build/puck.dsk &
   ```

   Agent auto-starts at boot (the Oberon log shows `Agent started`) — no manual step.

3. Connect the proxy (repeat freely; the device stays up):

   ```
   cd python
   # DeepSeek shown; any OpenAI-compatible API works:
   export PUCXY_API_KEY=...
   uv run pucxy --serial-in /tmp/p.in --serial-out /tmp/p.out \
       --base-url https://api.deepseek.com --model deepseek-v4-pro \
       "list the modules, then show me Agent.Mod"
   ```

   For OpenAI: `export OPENAI_API_KEY=...` and drop `--base-url/--model`.
   `deepseek-v4-flash` is cheaper; both V4 models support tool calls.
   (`deepseek-chat`/`deepseek-reasoner` are deprecated aliases, retire 2026-07-24.)

The proxy is a client — it never boots the emulator. Run `bin/risc` yourself (step 2)
and the proxy attaches to its serial line.

