# oberon-agent

Turn any Claude-Code-shaped LLM agent into a coding agent that operates on — and modifies —
a *live* Project Oberon 2013 or Extended Oberon system.

## The idea

Oberon is unusual: modules can be compiled, loaded, and (on EO, safely) unloaded *in the
running system*. With the right tools, an agent can change the live system from the
inside — edit a module, recompile, swap the old code out, swap the new code in, and keep
going without rebooting.

Two pieces wire this up:

- **`oat`** — *oberon-agent-tool* — a stateless Rust CLI. Each invocation opens the
  emulator's serial line, sends one PUT/GET/CALL request, prints the result, and exits.
- **`skill/oberon-agent/SKILL.md`** — the agent-facing rules. Drop it into
  `~/.claude/skills/oberon-agent/SKILL.md` (or symlink) and any Claude-Code session
  with `oat` on PATH becomes the agent.

```
LLM agent (Claude Code) -- runs `oat` -- RS232 wire protocol -- AgentTool.Mod on Oberon
```

The agent loop lives in the LLM. The Oberon side stays a small, dumb request/response
server (`Mod/{ProjectOberon,ExtendedOberon}/AgentTool.Mod`). `oat` translates wire ops
into named subcommands the agent invokes from its prompt.

See [`skill/oberon-agent/SKILL.md`](skill/oberon-agent/SKILL.md) for the working rules
and [`AGENTS.md`](AGENTS.md) for contributor notes.

## How the vendored pieces are used

Two git submodules under `vendor/`:

**[`vendor/risc-emu/`](https://github.com/zxygentoo/oberon-risc-emu-rs)** — Rust port of
Wirth's RISC5 emulator and host tools:

- `risc` — runs the emulator (the GUI window).
- `extract-source` — pulls Oberon source out of a stock disk image.
- `build-po-image` / `build-eo-image` — compile a source tree and produce a bootable
  `.dsk`. We use both — one per variant.
- `ob2txt` / `txt2ob` — convert between Oberon's CR-terminated module format and plain
  LF text, so patches stay readable in git.
- `DiskImage/Oberon-2020-08-18.dsk` — the stock PO2013 disk image we extract from.

**[`vendor/extended-oberon/`](https://github.com/andreaspirklbauer/Oberon-extended)** —
Andreas Pirklbauer's Extended Oberon distribution; `Documentation/S3RISCinstall.tar.gz`
is the stock EO disk image we extract from.

## Layout

```
oat/                          stateless Rust CLI (the only thing the agent shells out to)
Mod/
  ProjectOberon/              PO-flavored AgentTool.Mod + patches (System.Version, Oberon.Mod auto-load)
  ExtendedOberon/             EO-flavored AgentTool.Mod + patch (Oberon.Mod auto-load)
skill/oberon-agent/SKILL.md   agent-facing skill (single file; PO and EO covered)
DiskImage/                    build outputs: ProjectOberon.dsk, ExtendedOberon.dsk (gitignored)
vendor/                       submodules: risc-emu, extended-oberon
Makefile                      `make po-image`, `make eo-image`, `make image` (both), `make oberon`
```

## Quick start

### 1. Prereqs

- Rust toolchain (`cargo`).
- Standard Unix tools: `make`, `tar`, `patch`.

### 2. Clone with submodules

```
git clone --recurse-submodules https://github.com/zxygentoo/oberon-agent.git
cd oberon-agent
```

If you forgot `--recurse-submodules`:

```
git submodule update --init --recursive
```

### 3. Build the image(s)

```
make eo-image            # -> DiskImage/ExtendedOberon.dsk
make po-image            # -> DiskImage/ProjectOberon.dsk
make image               # both
```

The chain `tools` → `<v>-source` → `<v>-image` builds the Rust tools, extracts source
from the stock disk image, applies our patches, drops in `AgentTool.Mod`, and produces
a bootable `.dsk`. Cold build ~3–5 min per variant; warm rebuilds ~5 s.

### 4. Install `oat`

```
cargo install --path oat
```

(Or `cargo build --release` inside `oat/` and add `oat/target/release` to your PATH.)

### 5. Install the skill

```
ln -s "$PWD/skill/oberon-agent" ~/.claude/skills/oberon-agent
```

(Or copy. Claude Code picks new skills up on the next prompt — no restart needed.)

### 6. Boot the emulator

```
mkfifo /tmp/p.in /tmp/p.out          # once
make oberon                          # default: EO; override with VARIANT=po
```

`AgentTool.Mod` auto-installs at boot (our patched `Oberon.Mod` loads it after
`System`), so the wire server is listening as soon as you see the Oberon UI.

### 7. Drive it

In a Claude Code session:

```
$ oat --serial-in /tmp/p.in --serial-out /tmp/p.out check
ok: Extended Oberon System  AP 1.1.26 (round-trip 8ms)
```

Then ask the agent to do something — `act as oberon-agent and write a Hello.Mod that
prints "hello"`. The skill takes over from there.

For per-command help: `oat <subcommand> -h`.

## License

MIT — see [`LICENSE`](LICENSE).
