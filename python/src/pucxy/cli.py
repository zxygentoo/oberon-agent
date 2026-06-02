"""pucxy command line. See spec.md sections 4.5-4.6.

  pucxy [conn] [--model M] [--approve] ["task"]   # drive the agent (needs an LLM key)

Connection [conn] attaches to an already-running emulator's serial line:
  --serial PATH                  an existing serial device / PTY slave
  --serial-in F --serial-out F   the emulator's two --serial-in / --serial-out FIFOs
"""

import argparse
import os
import sys
from functools import partial

from . import transport as tp
from .agent import Agent, make_agent
from .agent import run as run_agent
from .console import AgentConsole
from .llm import make_llm


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except tp.TransportTimeout as e:
        print(f"\nserial timeout: {e}", file=sys.stderr)
        print(
            "The device didn't respond. Is the emulator running, and has 'Agent.Run' "
            "been executed in the Oberon window?",
            file=sys.stderr,
        )
        return 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pucxy", description="puck host proxy for Project Oberon 2013")
    p.add_argument("--serial", help="existing serial device / PTY slave")
    p.add_argument("--serial-in", dest="serial_in", help="FIFO the emulator reads (we write)")
    p.add_argument("--serial-out", dest="serial_out", help="FIFO the emulator writes (we read)")
    p.add_argument("--timeout", type=float, default=15.0, help="serial read timeout (s)")
    p.add_argument("--model", default=os.environ.get("PUCXY_MODEL", "gpt-4o"))
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    p.add_argument(
        "--api-key", default=None, help="LLM API key (else $PUCXY_API_KEY or $OPENAI_API_KEY)"
    )
    p.add_argument("--approve", action="store_true", help="confirm destructive tool calls")
    p.add_argument("--log", help="append a plain-text transcript of this session to FILE")
    p.add_argument("task", nargs="?", help="task prompt; omit for an interactive session")
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    t = _connect(args)
    console = AgentConsole(approve=args.approve, log_path=args.log)
    try:
        llm = make_llm(args.model, base_url=args.base_url, api_key=args.api_key)
        agent = make_agent(partial(tp.request, t), llm, console)
        if args.task:
            console.user_prompt(args.task)
            run_agent(agent, args.task)
        else:
            _repl(agent, console)
        return 0
    finally:
        console.close()
        tp.close(t)


def _repl(agent: Agent, console: AgentConsole) -> None:
    console.info("[dim]interactive session — type a task, or 'exit'/'quit'.[/]")
    while True:
        try:
            line = input("puck> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.info("")
            return
        if line in ("exit", "quit"):
            return
        if line:
            console.user_prompt(line)
            run_agent(agent, line)


def _connect(args: argparse.Namespace) -> tp.Transport:
    if args.serial:
        return tp.open_path(args.serial, timeout=args.timeout)
    if args.serial_in and args.serial_out:
        return tp.open_fifos(args.serial_in, args.serial_out, timeout=args.timeout)
    raise SystemExit("connect: pass --serial, or --serial-in and --serial-out")


if __name__ == "__main__":
    raise SystemExit(main())
