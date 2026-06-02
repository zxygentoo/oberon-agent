"""LLM streaming + tool-call accumulation. Tests the seam without hitting the wire."""

from types import SimpleNamespace

import pytest

from puck.llm import (
    LLM,
    LLMError,
    _StreamAccum,
    _accumulate_stream,
    _assistant_message,
    _merge_tool_call_delta,
    respond,
)


def delta(content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content, reasoning_content=reasoning_content, tool_calls=tool_calls
    )


def tcd(index=0, id=None, name=None, arguments=None):
    fn = None
    if name is not None or arguments is not None:
        fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=fn)


def chunk(d):
    return SimpleNamespace(choices=[SimpleNamespace(delta=d)])


# --- _merge_tool_call_delta ---


def test_merge_first_chunk_sets_id_and_name():
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1", name="read_file", arguments=""))
    assert calls[0] == {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": ""},
    }


def test_merge_argument_fragments_accumulate():
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1", name="read_file", arguments=""))
    _merge_tool_call_delta(calls, tcd(0, arguments='{"p'))
    _merge_tool_call_delta(calls, tcd(0, arguments='ath":'))
    _merge_tool_call_delta(calls, tcd(0, arguments='"M.Mod"}'))
    assert calls[0]["function"]["arguments"] == '{"path":"M.Mod"}'


def test_merge_name_can_arrive_in_fragments():
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1", name="rd"))
    _merge_tool_call_delta(calls, tcd(0, name="_file"))
    assert calls[0]["function"]["name"] == "rd_file"


def test_merge_multiple_parallel_tool_calls():
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1", name="read_file", arguments="{}"))
    _merge_tool_call_delta(calls, tcd(1, "c2", name="list_files", arguments="{}"))
    assert set(calls) == {0, 1}
    assert calls[0]["id"] == "c1"
    assert calls[1]["id"] == "c2"


def test_merge_empty_id_does_not_overwrite_existing():
    """`if tc.id:` skips falsy values, so later deltas without an id don't blank the slot."""
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1", name="x"))
    _merge_tool_call_delta(calls, tcd(0, None, arguments="..."))
    assert calls[0]["id"] == "c1"


def test_merge_handles_delta_with_no_function():
    """A bookkeeping delta carrying only an index/id (function=None) must not crash."""
    calls: dict = {}
    _merge_tool_call_delta(calls, tcd(0, "c1"))
    assert calls[0] == {
        "id": "c1",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    }


# --- _assistant_message ---


def test_assistant_message_text_only():
    accum = _StreamAccum(text=["hello ", "world"], reasoning=[], tool_calls={})
    assert _assistant_message(accum) == {"role": "assistant", "content": "hello world"}


def test_assistant_message_tool_calls_only_sets_content_none():
    accum = _StreamAccum(
        text=[],
        reasoning=[],
        tool_calls={
            0: {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        },
    )
    msg = _assistant_message(accum)
    assert msg["content"] is None
    assert len(msg["tool_calls"]) == 1


def test_assistant_message_tool_calls_sorted_by_index():
    accum = _StreamAccum(
        text=[],
        reasoning=[],
        tool_calls={2: {"id": "c3"}, 0: {"id": "c1"}, 1: {"id": "c2"}},
    )
    msg = _assistant_message(accum)
    assert [tc["id"] for tc in msg["tool_calls"]] == ["c1", "c2", "c3"]


def test_assistant_message_reasoning_echoed_when_present():
    accum = _StreamAccum(text=["t"], reasoning=["thinking ", "hard"], tool_calls={})
    msg = _assistant_message(accum)
    assert msg["reasoning_content"] == "thinking hard"


def test_assistant_message_omits_reasoning_key_when_absent():
    accum = _StreamAccum(text=["t"], reasoning=[], tool_calls={})
    assert "reasoning_content" not in _assistant_message(accum)


# --- _accumulate_stream ---


def test_accumulate_stream_text_invokes_on_text():
    received: list[str] = []
    stream = [chunk(delta(content="hello")), chunk(delta(content=" world"))]
    accum = _accumulate_stream(stream, received.append)
    assert accum.text == ["hello", " world"]
    assert received == ["hello", " world"]


def test_accumulate_stream_skips_chunks_with_no_choices():
    stream = [SimpleNamespace(choices=[]), chunk(delta(content="hi"))]
    accum = _accumulate_stream(stream, None)
    assert accum.text == ["hi"]


def test_accumulate_stream_collects_reasoning():
    stream = [chunk(delta(reasoning_content="think")), chunk(delta(reasoning_content=" more"))]
    accum = _accumulate_stream(stream, None)
    assert accum.reasoning == ["think", " more"]


def test_accumulate_stream_assembles_tool_call_from_fragments():
    stream = [
        chunk(delta(tool_calls=[tcd(0, "c1", name="read_file", arguments="")])),
        chunk(delta(tool_calls=[tcd(0, arguments='{"path":')])),
        chunk(delta(tool_calls=[tcd(0, arguments='"M.Mod"}')])),
    ]
    accum = _accumulate_stream(stream, None)
    assert accum.tool_calls[0] == {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"M.Mod"}'},
    }


# --- respond (mocking the OpenAI client) ---


class FakeCompletions:
    def __init__(self, stream=None, error=None):
        self.stream = stream
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.stream


def make_client(stream=None, error=None):
    completions = FakeCompletions(stream=stream, error=error)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_respond_wraps_client_errors_as_llm_error():
    client, _ = make_client(error=RuntimeError("network down"))
    with pytest.raises(LLMError) as ei:
        respond(LLM(client=client, model="m"), [])  # type: ignore[arg-type]
    assert "network down" in str(ei.value)


def test_respond_returns_assembled_assistant_message():
    client, _ = make_client(stream=[chunk(delta(content="ok"))])
    msg = respond(LLM(client=client, model="m"), [])  # type: ignore[arg-type]
    assert msg == {"role": "assistant", "content": "ok"}


def test_respond_forwards_on_text_per_chunk():
    received: list[str] = []
    client, _ = make_client(stream=[chunk(delta(content="a")), chunk(delta(content="b"))])
    respond(LLM(client=client, model="m"), [], on_text=received.append)  # type: ignore[arg-type]
    assert received == ["a", "b"]


def test_respond_passes_model_messages_tools_and_stream_flag():
    client, completions = make_client(stream=[chunk(delta(content="x"))])
    msgs = [{"role": "user", "content": "hi"}]
    respond(LLM(client=client, model="my-model"), msgs)  # type: ignore[arg-type]
    kw = completions.calls[0]
    assert kw["model"] == "my-model"
    assert kw["messages"] == msgs
    assert kw["stream"] is True
    assert isinstance(kw["tools"], list)
