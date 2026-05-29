"""Human operator console (rich). See spec.md section 4.5.

Renders a live transcript (assistant text, tool calls/results) and, in approve
mode, gates destructive tool calls.
"""

from __future__ import annotations

import json

from rich.console import Console


class AgentConsole:
    def __init__(self, approve: bool = False):
        self.c = Console()
        self.approve = approve
        self._streaming = False

    def stream_text(self, chunk: str) -> None:
        if not self._streaming:
            self.c.print("[bold cyan]assistant[/] ", end="")
            self._streaming = True
        self.c.print(chunk, end="", markup=False, soft_wrap=True, highlight=False)

    def end_text(self) -> None:
        if self._streaming:
            self.c.print()
            self._streaming = False

    def tool_call(self, name: str, args: dict) -> None:
        self.c.print(f"[bold yellow]→ {name}[/] [dim]{_short(args)}[/]")

    def tool_result(self, name: str, result: dict) -> None:
        if isinstance(result, dict) and "error" in result:
            self.c.print(f"  [red]error[/] {_short(result)}")
            return
        if isinstance(result, dict):
            # compiler log / command output: small and the actual result — show verbatim
            # (real newlines, no truncation, markup off since it may contain '[').
            for key in ("output", "log"):
                body = result.get(key)
                if isinstance(body, str):
                    self.c.print("  [green]ok[/]")
                    self.c.print(body, markup=False, highlight=False, soft_wrap=True)
                    rest = {k: v for k, v in result.items() if k != key}
                    if rest:
                        self.c.print(f"  [dim]{_short(rest, limit=2000)}[/]")
                    return
            # file content: just a summary — reads are usually for the agent's own
            # reference; it prints the content itself when the operator asks to see it.
            body = result.get("content")
            if isinstance(body, str):
                lines = body.count("\n") + 1
                self.c.print(f"  [green]ok[/] [dim]read {len(body)} bytes, {lines} lines[/]")
                return
        self.c.print(f"  [green]ok[/] [dim]{_short(result, limit=2000)}[/]")

    def confirm(self, name: str, args: dict, destructive: bool = False) -> bool:
        if not (self.approve and destructive):
            return True
        from rich.prompt import Confirm

        return Confirm.ask(f"  run [bold]{name}[/]?", default=True)

    def info(self, text: str) -> None:
        self.c.print(text)

    def error(self, text: str) -> None:
        self.c.print(f"[red]{text}[/]")


def _short(obj: object, limit: int = 240) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"
