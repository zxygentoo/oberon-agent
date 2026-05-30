"""Human operator console + optional transcript logger. See spec.md section 4.5.

Renders a live transcript (assistant text, tool calls/results) and, in approve
mode, gates destructive tool calls. With --log, also mirrors every event to a
plain-text transcript file for offline iteration / crash inspection.
"""

import json
from datetime import datetime
from typing import IO

from rich.console import Console
from rich.prompt import Confirm


class AgentConsole:
    """Owns the live terminal + (optional) transcript log file."""

    def __init__(self, approve: bool = False, log_path: str | None = None):
        self.c = Console()
        self.approve = approve
        self._streaming = False
        self._asst_buf: list[str] = []
        self._log: IO[str] | None = None
        if log_path:
            self._log = open(log_path, "a", encoding="utf-8", buffering=1)
            self._write("SESSION", "started")

    def user_prompt(self, text: str) -> None:
        self._write("USER", text)

    def stream_text(self, chunk: str) -> None:
        if not self._streaming:
            self.c.print("[bold cyan]assistant[/] ", end="")
            self._streaming = True
        self.c.print(chunk, end="", markup=False, soft_wrap=True, highlight=False)
        self._asst_buf.append(chunk)

    def end_text(self) -> None:
        if not self._streaming:
            return
        self.c.print()
        self._streaming = False
        if self._asst_buf:
            self._write("ASSISTANT", "".join(self._asst_buf))
            self._asst_buf = []

    def tool_call(self, name: str, args: dict) -> None:
        self.c.print(f"[bold yellow]→ {name}[/] [dim]{_short(args)}[/]")
        self._write(f"TOOL_CALL {name}", json.dumps(args, default=str, ensure_ascii=False))

    def tool_result(self, name: str, result: dict) -> None:
        self._write(f"TOOL_RESULT {name}", json.dumps(result, default=str, ensure_ascii=False))
        _render_tool_result(self.c, result)

    def confirm(self, name: str, args: dict, destructive: bool = False) -> bool:
        if not (self.approve and destructive):
            return True
        return Confirm.ask(f"  run [bold]{name}[/] [dim]{_short(args)}[/]?", default=True)

    def info(self, text: str) -> None:
        self.c.print(text)

    def error(self, text: str) -> None:
        self.c.print(f"[red]{text}[/]")
        self._write("ERROR", text)

    def close(self) -> None:
        if not self._log:
            return
        self._write("SESSION", "ended")
        self._log.close()
        self._log = None

    def _write(self, event: str, body: str = "") -> None:
        if not self._log:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {event}"
        if body:
            line += "  " + body.replace("\n", "\n    ")
        self._log.write(line + "\n")


def _render_tool_result(c: Console, result: dict) -> None:
    if "error" in result:
        c.print(f"  [red]error[/] {_short(result)}")
        return
    if _render_verbatim_body(c, result):
        return
    if _render_content_summary(c, result):
        return
    c.print(f"  [green]ok[/] [dim]{_short(result, limit=2000)}[/]")


def _render_verbatim_body(c: Console, result: dict) -> bool:
    """Compiler log / command output: small and the actual result — show verbatim
    (real newlines, no truncation, markup off since it may contain '[')."""
    for key in ("output", "log"):
        body = result.get(key)
        if isinstance(body, str):
            c.print("  [green]ok[/]")
            c.print(body, markup=False, highlight=False, soft_wrap=True)
            rest = {k: v for k, v in result.items() if k != key}
            if rest:
                c.print(f"  [dim]{_short(rest, limit=2000)}[/]")
            return True
    return False


def _render_content_summary(c: Console, result: dict) -> bool:
    """File content: just a summary — reads are usually for the agent's own reference;
    the model prints the body itself when the operator asks to see it."""
    body = result.get("content")
    if not isinstance(body, str):
        return False
    lines = body.count("\n") + 1
    c.print(f"  [green]ok[/] [dim]read {len(body)} bytes, {lines} lines[/]")
    return True


def _short(obj: object, limit: int = 240) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"
