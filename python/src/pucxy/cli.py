"""pucxy command line. See spec.md sections 4.5-4.6.

  pucxy run  [conn] [--model M] [--approve] ["task"]   # drive the agent (needs an LLM key)
  pucxy tool [conn] NAME [JSON_ARGS]                    # invoke one tool, no LLM (smoke testing)

Connection [conn] is one of:
  --serial PATH                  connect to an existing serial device / PTY slave
  --serial-in F --serial-out F   two FIFOs (emulator's --serial-in / --serial-out)
  --image IMG [--risc bin/risc]  spawn the emulator on a fresh raw PTY
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .console import AgentConsole
from .supervisor import Supervisor
from .tools import AgentTools
from .transport import Transport, TransportTimeout, open_fifos, open_path, open_pty

if TYPE_CHECKING:
    from .agent import Agent

REPO = Path(__file__).resolve().parents[3]  # .../python/src/pucxy/cli.py -> repo root


def _add_conn(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--serial", help="existing serial device / PTY slave")
    sp.add_argument("--serial-in", dest="serial_in", help="FIFO the emulator reads (we write)")
    sp.add_argument("--serial-out", dest="serial_out", help="FIFO the emulator writes (we read)")
    sp.add_argument(
        "--risc", default=str(REPO / "bin" / "risc"), help="emulator binary to spawn (with --image)"
    )
    sp.add_argument("--image", help="disk image to boot; spawns the emulator on a PTY")
    sp.add_argument("--mem", type=int, help="emulator RAM in MB")
    sp.add_argument("--timeout", type=float, default=15.0, help="serial read timeout (s)")


def _connect(args: argparse.Namespace) -> tuple[Transport, Supervisor | None]:
    if args.serial:
        return open_path(args.serial, timeout=args.timeout), None
    if args.serial_in and args.serial_out:
        return open_fifos(args.serial_in, args.serial_out, timeout=args.timeout), None
    if args.image:
        transport, slave = open_pty(timeout=args.timeout)
        sup = Supervisor(args.risc, args.image, slave, mem=args.mem)
        sup.start()
        return transport, sup
    raise SystemExit("connect: pass --serial, or --serial-in/--serial-out, or --image")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pucxy", description="puck host proxy for Project Oberon 2013")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="drive the agent (LLM) against the Oberon system")
    _add_conn(run)
    run.add_argument("--model", default=os.environ.get("PUCXY_MODEL", "gpt-4o"))
    run.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    run.add_argument(
        "--api-key", default=None, help="LLM API key (else $PUCXY_API_KEY or $OPENAI_API_KEY)"
    )
    run.add_argument("--approve", action="store_true", help="confirm destructive tool calls")
    run.add_argument("task", nargs="?", help="task prompt; omit for an interactive session")

    tool = sub.add_parser("tool", help="invoke one tool directly (no LLM)")
    _add_conn(tool)
    tool.add_argument("name", help="tool name, e.g. read_file")
    tool.add_argument(
        "json_args", nargs="?", default="{}", help='JSON object, e.g. {"path":"Agent.Mod"}'
    )

    args = p.parse_args(argv)
    try:
        return _cmd_tool(args) if args.cmd == "tool" else _cmd_run(args)
    except TransportTimeout as e:
        print(f"\nserial timeout: {e}", file=sys.stderr)
        print(
            "The device didn't respond. Is the emulator running, and has 'Agent.Run' "
            "been executed in the Oberon window?",
            file=sys.stderr,
        )
        return 1


def _cmd_tool(args: argparse.Namespace) -> int:
    transport, sup = _connect(args)
    try:
        result = AgentTools(transport).dispatch(args.name, json.loads(args.json_args))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not (isinstance(result, dict) and "error" in result) else 1
    finally:
        transport.close()
        if sup:
            sup.stop()


def _cmd_run(args: argparse.Namespace) -> int:
    from .agent import Agent
    from .llm import LLMClient

    transport, sup = _connect(args)
    console = AgentConsole(approve=args.approve)
    try:
        agent = Agent(
            AgentTools(transport),
            LLMClient(args.model, base_url=args.base_url, api_key=args.api_key),
            console,
        )
        if args.task:
            agent.run(args.task)
        else:
            _repl(agent, console)
        return 0
    finally:
        transport.close()
        if sup:
            sup.stop()


def _repl(agent: "Agent", console: AgentConsole) -> None:
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
            agent.run(line)


if __name__ == "__main__":
    raise SystemExit(main())
