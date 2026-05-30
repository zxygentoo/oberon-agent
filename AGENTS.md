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

## Style preferences

- Prefer short, focused functions. If a block of logic has a clear purpose, extract it — even if it's only called once. Give it a good name and place it right below the caller. Single-use helpers are fine.
- Strongly prefer functional style. Use classes only when they genuinely manage state at the outer boundary of the system. Dataclasses for structured data are fine, custom Exceptions are fine. Default to plain functions + modules.
- Python 3.12: native `X | None`, `list[X]`, `collections.abc.Iterator`. No `from __future__ import annotations`.
- LBYL over EAFP where practical.
- **Avoid lazy imports if a top-level import works.** Function-scoped `import` is fine *during* incremental editing for speed (don't break flow to hoist on every edit), but post-check at natural pauses — at the end of a feature, before pushing, and especially during refactor passes — and hoist anything that doesn't actually need to be lazy. Reserve in-function imports for the cases that do require them: import-time cycles, optional/heavy deps, or platform-conditional imports.
