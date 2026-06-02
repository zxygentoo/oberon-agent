"""Agent tool-call loop. Scripts `agent.respond` so we exercise the loop end-to-end
through the real `tools.dispatch` against an in-memory FakeTransport."""

import json

from fake import FakeTransport

from puck import agent
from puck.agent import Agent, make_agent, run
from puck.llm import LLM, LLMError


class FakeConsole:
    def __init__(self):
        self.streamed: list[str] = []
        self.end_text_calls = 0
        self.tool_calls: list[tuple[str, dict]] = []
        self.tool_results: list[tuple[str, dict]] = []
        self.errors: list[str] = []

    def stream_text(self, chunk: str) -> None:
        self.streamed.append(chunk)

    def end_text(self) -> None:
        self.end_text_calls += 1

    def tool_call(self, name: str, args: dict) -> None:
        self.tool_calls.append((name, args))

    def tool_result(self, name: str, result: dict) -> None:
        self.tool_results.append((name, result))

    def error(self, text: str) -> None:
        self.errors.append(text)


def make_test_agent(device=None, max_steps=10) -> Agent:
    return Agent(
        device=device or FakeTransport(),
        llm=LLM(client=None, model="fake"),  # type: ignore[arg-type]
        console=FakeConsole(),
        messages=[{"role": "system", "content": "sys"}],
        max_steps=max_steps,
    )


def script(monkeypatch, *responses) -> None:
    """Make `agent.respond` return scripted values in order; raise if an Exception is scripted."""
    it = iter(responses)

    def fake_respond(llm, messages, on_text=None):
        r = next(it)
        if isinstance(r, BaseException):
            raise r
        return r

    monkeypatch.setattr(agent, "respond", fake_respond)


def asst_text(text: str) -> dict:
    return {"role": "assistant", "content": text}


def asst_tools(*calls) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
            for cid, name, args in calls
        ],
    }


def test_make_agent_seeds_system_message():
    a = make_agent(FakeTransport(), LLM(None, "m"), FakeConsole(), system_prompt="hello")  # type: ignore[arg-type]
    assert a.messages == [{"role": "system", "content": "hello"}]


def test_no_tool_calls_returns_content(monkeypatch):
    a = make_test_agent()
    script(monkeypatch, asst_text("done"))

    assert run(a, "do thing") == "done"
    assert a.messages[-2:] == [
        {"role": "user", "content": "do thing"},
        {"role": "assistant", "content": "done"},
    ]


def test_one_tool_call_then_text(monkeypatch):
    device = FakeTransport({"M.Mod": b"MODULE M;\rEND M.\r"})
    a = make_test_agent(device=device)
    script(
        monkeypatch,
        asst_tools(("c1", "read_file", json.dumps({"path": "M.Mod"}))),
        asst_text("read it"),
    )

    assert run(a, "read") == "read it"
    tool_msg = next(m for m in a.messages if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["content"] == "MODULE M;\nEND M.\n"
    assert a.console.tool_calls == [("read_file", {"path": "M.Mod"})]
    assert len(a.console.tool_results) == 1


def test_multiple_tool_calls_in_one_turn(monkeypatch):
    device = FakeTransport({"A.Mod": b"a\r", "B.Mod": b"b\r"})
    a = make_test_agent(device=device)
    script(
        monkeypatch,
        asst_tools(
            ("c1", "read_file", json.dumps({"path": "A.Mod"})),
            ("c2", "read_file", json.dumps({"path": "B.Mod"})),
        ),
        asst_text("both"),
    )

    run(a, "read both")
    tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert [c[1]["path"] for c in a.console.tool_calls] == ["A.Mod", "B.Mod"]


def test_max_steps_reached(monkeypatch):
    a = make_test_agent(max_steps=3)
    forever = asst_tools(("c1", "list_files", "{}"))
    script(monkeypatch, forever, forever, forever)

    assert run(a, "loop") is None
    assert a.console.errors == ["reached max steps"]


def test_invalid_json_args_fed_back_as_tool_error(monkeypatch):
    a = make_test_agent()
    script(
        monkeypatch,
        asst_tools(("c1", "read_file", "{not json")),
        asst_text("ok"),
    )

    run(a, "x")
    tool_msg = next(m for m in a.messages if m.get("role") == "tool")
    assert "invalid tool arguments JSON" in json.loads(tool_msg["content"])["error"]
    # The dispatcher never ran, so the console didn't see a tool_call/tool_result.
    assert a.console.tool_calls == []
    assert a.console.tool_results == []


def test_unknown_tool_name_fed_back_as_tool_error(monkeypatch):
    a = make_test_agent()
    script(
        monkeypatch,
        asst_tools(("c1", "nonexistent", "{}")),
        asst_text("ok"),
    )

    run(a, "x")
    tool_msg = next(m for m in a.messages if m.get("role") == "tool")
    assert "unknown tool" in json.loads(tool_msg["content"])["error"]


def test_empty_arguments_default_to_empty_dict(monkeypatch):
    """Some providers stream zero-arg tool calls with arguments=''. The agent treats
    that as `{}` via `json.loads(arguments or '{}')`."""
    a = make_test_agent()
    script(
        monkeypatch,
        asst_tools(("c1", "list_modules", "")),
        asst_text("ok"),
    )

    run(a, "what's loaded")
    tool_msg = next(m for m in a.messages if m.get("role") == "tool")
    assert "modules" in json.loads(tool_msg["content"])


def test_llm_error_rolls_back_user_turn(monkeypatch):
    a = make_test_agent()
    before = list(a.messages)
    script(monkeypatch, LLMError("boom"))

    assert run(a, "x") is None
    assert a.messages == before  # user message rolled back, REPL stays clean
    assert a.console.errors == ["LLM error: boom"]


def test_llm_error_mid_loop_rolls_back_partial_state(monkeypatch):
    """An error on the second respond() must unwind the user + assistant + tool
    messages from this turn, not leave an orphaned tool result."""
    a = make_test_agent()
    before = list(a.messages)
    script(
        monkeypatch,
        asst_tools(("c1", "list_files", "{}")),
        LLMError("mid-loop"),
    )

    assert run(a, "x") is None
    assert a.messages == before
