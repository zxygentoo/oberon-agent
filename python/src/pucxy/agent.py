"""The Phase-1 agent loop (runs on the host). See spec.md sections 3-4.

Tool-call loop: ask the model, execute any tool calls against the Oberon system,
feed results back, repeat until the model stops calling tools.
"""

import json
from dataclasses import dataclass, field

from . import tools
from .console import AgentConsole
from .llm import LLM, LLMError, respond

DEFAULT_SYSTEM = """\
You are puck, a coding agent operating inside a LIVE Extended Oberon system through a set of \
tools. The system runs Oberon-2 (2020 Edition) — a superset of Oberon-07 that adds type-bound \
procedures, a FINAL block, and safe module unloading. Modules can be compiled and \
loaded/unloaded while it runs, so you can change the live system from the inside.

Working rules:
- Write plain-ASCII Oberon source. Module M lives in file 'M.Mod'. Oberon-07 idioms are a strict \
subset and compile fine; reach for type-bound procedures and FINAL when the design calls for them.
- To put new code into effect: compile (use new_symbol=true when a module's exported interface \
changed), then load_module. To replace a *running* module, unload_module first then load_module.
- unload_module uses EO safe-unload (System.Free /f). If the module has no live references it is \
fully removed; if it does (open viewers, heap objects of its types) it is HIDDEN — renamed to \
'*<name>' with its memory kept valid — so dangling-pointer crashes are impossible. A subsequent \
load_module then allocates a fresh block: safe live reload. The hidden copy is reclaimed \
automatically once unreferenced (Modules.Collect runs in the GC task). unload_module fails only \
when other loaded modules still import this one.
- For any module with viewers or installed tasks, declare a FINAL block that closes them — the \
system runs FINAL when the module is actually unloaded from memory (after Hide → Collect). Hold \
references to your viewers/tasks in module-level vars so FINAL can reach them. Example: \
`BEGIN ... FINAL Viewers.Close(myV); Oberon.Remove(myT) END M.`
- A module loads on demand: run_command "Mod.Proc" loads Mod from its .rsc and runs Proc. So to \
run an already-compiled module just run_command it — no load_module first, and don't compile \
unless you changed the source. Note: Mod.Open-style commands open a NEW viewer on every call, so \
invoke them once.
- Do NOT run_command System.Close to close a viewer headlessly — it tests Oberon.Par.vwr.dsc = \
Par.frame, which the dummy frame in headless CALLs doesn't satisfy, so it no-ops. Implement your \
module's own Close* command that holds a saved viewer reference and calls Viewers.Close directly.
- compile returns the compiler's raw log: error lines 'pos <offset> <msg>' then \
'compilation FAILED', or a success line. You hold the source, so use the message to localize.
- Prefer the named tools; use run_command only as an escape hatch.
- The operator's console shows tool output live: compiler logs and command output in full, but \
file reads as only a one-line summary. So DO print a file's content when the operator asks to \
see it; don't re-echo logs or command output the console already shows.
Be concise. Verify your work by compiling and running."""


@dataclass
class Agent:
    device: tools.Device
    llm: LLM
    console: AgentConsole
    messages: list[dict] = field(default_factory=list)
    max_steps: int = 60


def make_agent(
    device: tools.Device,
    llm: LLM,
    console: AgentConsole,
    system_prompt: str = DEFAULT_SYSTEM,
    max_steps: int = 60,
) -> Agent:
    """Build an agent seeded with the system message."""
    return Agent(
        device=device,
        llm=llm,
        console=console,
        messages=[{"role": "system", "content": system_prompt}],
        max_steps=max_steps,
    )


def run(agent: Agent, task: str) -> str | None:
    """Drive one user turn to completion (or roll back if the LLM errors)."""
    before = len(agent.messages)
    agent.messages.append({"role": "user", "content": task})
    try:
        return _drive_loop(agent)
    except LLMError as e:
        # Roll the conversation back to before this turn so the REPL stays usable and
        # the next user input doesn't sit next to an orphaned user/tool pair.
        agent.console.end_text()
        agent.console.error(f"LLM error: {e}")
        del agent.messages[before:]
        return None


def _drive_loop(agent: Agent) -> str | None:
    for _ in range(agent.max_steps):
        msg = respond(agent.llm, agent.messages, on_text=agent.console.stream_text)
        agent.console.end_text()
        agent.messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content")
        for tc in tool_calls:
            agent.messages.append(_run_tool(agent, tc))
    agent.console.error("reached max steps")
    return None


def _run_tool(agent: Agent, tc: dict) -> dict:
    name = tc["function"]["name"]
    try:
        args = json.loads(tc["function"]["arguments"] or "{}")
    except json.JSONDecodeError as e:
        result: dict = {"error": f"invalid tool arguments JSON: {e}"}
    else:
        result = _execute_tool(agent, name, args)
    return {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)}


def _execute_tool(agent: Agent, name: str, args: dict) -> dict:
    """Print the call, dispatch, print the result."""
    agent.console.tool_call(name, args)
    result = tools.dispatch(agent.device, name, args)
    agent.console.tool_result(name, result)
    return result
