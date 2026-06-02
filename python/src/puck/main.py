"""Command-line entry + human operator console. See spec.md sections 4.5-4.6.

Click drives the CLI; rich renders a live transcript (assistant text, tool
calls/results). With --log, also mirrors every event to a plain-text transcript
file for offline iteration / crash inspection.
"""

import json
import sys
from datetime import datetime
from functools import partial
from typing import IO

import click
from rich.console import Console

from . import transport as tp
from .agent import Agent, make_agent
from .agent import run as run_agent
from .llm import make_llm

# Provider -> (base_url, default model). base_url=None uses the OpenAI SDK's default.
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-v4-pro"),
    "openai": (None, "gpt-5.5"),
    "claude": ("https://api.anthropic.com/v1/", "claude-opus-4-8"),
}


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--serial", help="existing serial device / PTY slave")
@click.option("--serial-in", "serial_in", help="FIFO the emulator reads (we write)")
@click.option("--serial-out", "serial_out", help="FIFO the emulator writes (we read)")
@click.option(
    "--timeout", type=float, default=15.0, show_default=True, help="serial read timeout (s)"
)
@click.option(
    "--model",
    type=click.Choice(list(PROVIDERS)),
    default="deepseek",
    show_default=True,
    help="LLM provider",
)
@click.option("--api-key", envvar="LLM_API_KEY", help="LLM API key (else $LLM_API_KEY)")
@click.option(
    "--log",
    "log_path",
    type=click.Path(dir_okay=False),
    help="append a plain-text transcript of this session to FILE",
)
@click.argument("task", required=False)
def main(
    serial: str | None,
    serial_in: str | None,
    serial_out: str | None,
    timeout: float,
    model: str,
    api_key: str | None,
    log_path: str | None,
    task: str | None,
) -> None:
    """puck — host proxy for the puck coding agent on Extended Oberon.

    Attaches to an already-running emulator's serial line. Pass a TASK to run
    once, or omit for an interactive session.
    """
    if not api_key:
        raise click.UsageError("set $LLM_API_KEY or pass --api-key")
    try:
        _run(serial, serial_in, serial_out, timeout, model, api_key, log_path, task)
    except tp.TransportTimeout as e:
        click.secho(f"\nserial timeout: {e}", fg="red", err=True)
        click.echo("The device didn't respond. Is the emulator running?", err=True)
        sys.exit(1)


def _run(
    serial: str | None,
    serial_in: str | None,
    serial_out: str | None,
    timeout: float,
    provider: str,
    api_key: str,
    log_path: str | None,
    task: str | None,
) -> None:
    t = _connect(serial, serial_in, serial_out, timeout)
    console = AgentConsole(log_path=log_path)
    try:
        base_url, model_name = PROVIDERS[provider]
        llm = make_llm(model_name, base_url=base_url, api_key=api_key)
        agent = make_agent(partial(tp.request, t), llm, console)
        if task:
            console.user_prompt(task)
            run_agent(agent, task)
        else:
            _repl(agent, console)
    finally:
        console.close()
        tp.close(t)


def _repl(agent: Agent, console: "AgentConsole") -> None:
    console.info("[dim]interactive session — type a task, or 'exit'/'quit'.[/]")
    while True:
        try:
            line = console.c.input("[bold green]puck>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.info("")
            return
        if line in ("exit", "quit"):
            return
        if line:
            console.user_prompt(line)
            run_agent(agent, line)


def _connect(
    serial: str | None, serial_in: str | None, serial_out: str | None, timeout: float
) -> tp.Transport:
    if serial:
        return tp.open_path(serial, timeout=timeout)
    if serial_in and serial_out:
        return tp.open_fifos(serial_in, serial_out, timeout=timeout)
    raise click.UsageError("pass --serial, or --serial-in and --serial-out")


# --- operator console ---


class AgentConsole:
    """Owns the live terminal + (optional) transcript log file."""

    def __init__(self, log_path: str | None = None):
        self.c = Console()
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


if __name__ == "__main__":
    main()
