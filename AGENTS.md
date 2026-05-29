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

- Oberon side: Oberon-07 (as in PO2013), sources in `oberon/`. Host proxy: Python, in `python/`
  (a `uv` project; package `pucxy`).
- Reference material (gitignored, local): PO2013 source in `po2013/`, the book in
  `book/`, the built emulator + tools in `bin/` (`risc`, `build-image`).
- *(more as they emerge)*
