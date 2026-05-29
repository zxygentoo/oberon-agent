"""The Phase-1 agent loop (runs on the host). See spec.md sections 3-4.

Tool-call loop: ask the model, execute any tool calls against the Oberon system,
feed results back, repeat until the model stops calling tools.
"""

from __future__ import annotations

import json

DEFAULT_SYSTEM = """\
You are puck, a coding agent operating inside a LIVE Project Oberon 2013 system \
through a set of tools. The system is written in Oberon-07; modules can be \
compiled and loaded/unloaded while it runs, so you can change the live system \
from the inside.

Working rules:
- Write plain-ASCII Oberon-07 source. Module M lives in file 'M.Mod'.
- To put new code into effect: compile (use new_symbol=true when a module's \
exported interface changed), then load_module. To replace a running module, \
unload_module first (it fails while the module is still imported), then load_module.
- compile returns the compiler's raw log: error lines 'pos <offset> <msg>' then \
'compilation FAILED', or a success line. You already have the source, so use the message to localize.
- Prefer the named tools; use run_command only as an escape hatch.
Be concise. Verify your work by compiling and running."""

DESTRUCTIVE = frozenset({"delete_file", "unload_module", "run_command"})


class Agent:
    def __init__(
        self, tools, llm, console, system_prompt: str = DEFAULT_SYSTEM, max_steps: int = 60
    ):
        self.tools = tools
        self.llm = llm
        self.console = console
        self.max_steps = max_steps
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def run(self, task: str) -> str | None:
        self.messages.append({"role": "user", "content": task})
        for _ in range(self.max_steps):
            msg = self.llm.respond(self.messages, on_text=self.console.stream_text)
            self.console.end_text()
            self.messages.append(msg)
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                return msg.get("content")
            for tc in tool_calls:
                self.messages.append(self._run_tool(tc))
        self.console.error("reached max steps")
        return None

    def _run_tool(self, tc: dict) -> dict:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError as e:
            result = {"error": f"invalid tool arguments JSON: {e}"}
        else:
            self.console.tool_call(name, args)
            if not self.console.confirm(name, args, destructive=name in DESTRUCTIVE):
                result = {"error": "denied by operator"}
            else:
                result = self.tools.dispatch(name, args)
            self.console.tool_result(name, result)
        return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)}
