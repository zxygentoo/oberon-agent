# pucxy

Host-side proxy for the **puck** coding agent on Project Oberon 2013.

It owns HTTPS + JSON to the LLM and speaks a minimal `PUT`/`GET`/`CALL` wire
protocol over Oberon's serial line to `Agent.Mod`. See the top-level
[`spec.md`](../spec.md) for the design.

```
uv sync
uv run pytest          # unit tests (no emulator needed)
uv run pucxy --help
```

Layout: `src/pucxy/` (package), `tests/` (unit tests), `scripts/` (deploy helper).

## Bring-up (Phase 1)

The emulator is a long-lived GUI process; the proxy is a client that connects to its
serial line. Run them **separately** — boot once, run `Agent.Run` once, then drive as
often as you like (no reboot per task).

1. Build an image with `Agent` baked in (source **and** a precompiled `Agent.rsc`):

   ```
   uv run python scripts/build_image.py      # -> ../build/puck.dsk
   ```

   `bin/build-image` only compiles a fixed module list, so the helper cross-compiles
   `Agent.Mod` via a leaf-module slot and installs the resulting `Agent.rsc`/`.smb`.
   Use `--no-precompile` to ship source only (then run `ORP.Compile Agent.Mod/s` on
   the device before step 3).

2. Boot the emulator on two FIFOs (from the repo root) and start the server:

   ```
   mkfifo /tmp/p.in /tmp/p.out                 # once
   ./bin/risc --serial-in /tmp/p.in --serial-out /tmp/p.out build/puck.dsk &
   ```

   Then in the Oberon window run `Agent.Run` (Log shows: `Agent started`).

3. Connect the proxy (repeat freely; the device stays up):

   ```
   cd python
   # no LLM needed — direct wire smoke test:
   uv run pucxy tool --serial-in /tmp/p.in --serial-out /tmp/p.out list_modules
   uv run pucxy tool --serial-in /tmp/p.in --serial-out /tmp/p.out read_file '{"path":"Agent.Mod"}'

   # full agent (DeepSeek shown; any OpenAI-compatible API works):
   export PUCXY_API_KEY=...
   uv run pucxy run --serial-in /tmp/p.in --serial-out /tmp/p.out \
       --base-url https://api.deepseek.com --model deepseek-v4-pro \
       "list the modules, then show me Agent.Mod"
   ```

   For OpenAI: `export OPENAI_API_KEY=...` and drop `--base-url/--model`.
   `deepseek-v4-flash` is cheaper; both V4 models support tool calls.
   (`deepseek-chat`/`deepseek-reasoner` are deprecated aliases, retire 2026-07-24.)

`--image IMG` is a convenience that spawns its own emulator on a private PTY — fine
for an *interactive* session (`pucxy run --image …`, then `Agent.Run` in the window,
then type at `puck>`). Don't pass a one-shot task with `--image`: the agent starts
driving before the device has booted and `Agent.Run` has run, so it times out.
Connect mode (above) avoids that.

