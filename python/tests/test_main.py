"""Operator console and tool-result rendering. The log-file behavior is the
load-bearing bit; rendering is asserted on the dispatch booleans plus a couple
of plain-text checks against a no-color rich Console."""

import io

from rich.console import Console

from puck.main import (
    AgentConsole,
    _render_content_summary,
    _render_tool_result,
    _render_verbatim_body,
    _short,
)


def quiet_console() -> tuple[Console, io.StringIO]:
    """A Console that writes plain text into an in-memory buffer."""
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, no_color=True, width=200), buf


# --- _short ---


def test_short_under_limit_is_pass_through():
    assert _short({"a": 1}) == '{"a": 1}'


def test_short_over_limit_is_truncated_with_ellipsis():
    s = _short({"x": "y" * 500}, limit=50)
    assert len(s) == 50
    assert s.endswith("…")


def test_short_handles_non_json_serializable_via_default_str():
    """`default=str` stringifies anything that isn't JSON-native (e.g. bytes)."""
    s = _short({"b": b"hi"})
    assert "hi" in s


# --- _render_verbatim_body / _render_content_summary dispatch booleans ---


def test_verbatim_body_renders_output_key():
    c, _ = quiet_console()
    assert _render_verbatim_body(c, {"output": "compilation log\n"}) is True


def test_verbatim_body_renders_log_key():
    c, _ = quiet_console()
    assert _render_verbatim_body(c, {"log": "command output\n"}) is True


def test_verbatim_body_skips_when_neither_key_is_a_string():
    c, _ = quiet_console()
    assert _render_verbatim_body(c, {"output": 42}) is False
    assert _render_verbatim_body(c, {"log": None}) is False
    assert _render_verbatim_body(c, {"other": "x"}) is False


def test_content_summary_only_for_string_content():
    c, buf = quiet_console()
    assert _render_content_summary(c, {"content": "abc\ndef"}) is True
    assert "7 bytes, 2 lines" in buf.getvalue()


def test_content_summary_returns_false_for_non_string_or_missing():
    c, _ = quiet_console()
    assert _render_content_summary(c, {"content": 123}) is False
    assert _render_content_summary(c, {}) is False


def test_render_tool_result_error_takes_priority():
    c, buf = quiet_console()
    _render_tool_result(c, {"error": "boom", "log": "details"})
    out = buf.getvalue()
    assert "error" in out
    assert "boom" in out
    # The verbatim "log" body must NOT also print — error short-circuits.
    assert "details" not in out.split("error", 1)[1] or out.count("boom") == 1


def test_render_tool_result_falls_through_to_generic_ok():
    c, buf = quiet_console()
    _render_tool_result(c, {"ok": True, "module": "X"})
    assert "ok" in buf.getvalue()


# --- AgentConsole log file ---


def test_console_no_log_when_path_is_none(tmp_path):
    """No log_path: methods still work, no file created in the test dir."""
    log_dir = tmp_path / "noLog"
    log_dir.mkdir()
    console = AgentConsole(log_path=None)
    console.c, _ = quiet_console()
    console.user_prompt("hi")
    console.error("oops")
    console.close()
    assert list(log_dir.iterdir()) == []


def test_console_log_records_session_user_assistant_tool_and_error(tmp_path):
    log = tmp_path / "session.log"
    console = AgentConsole(log_path=str(log))
    console.c, _ = quiet_console()

    console.user_prompt("task1")
    console.stream_text("hello ")
    console.stream_text("world")
    console.end_text()
    console.tool_call("read_file", {"path": "M.Mod"})
    console.tool_result("read_file", {"content": "MODULE M;\nEND M.\n"})
    console.error("bad thing")
    console.close()

    text = log.read_text()
    assert "SESSION  started" in text
    assert "USER  task1" in text
    assert "ASSISTANT  hello world" in text
    assert "TOOL_CALL read_file" in text
    assert '"path": "M.Mod"' in text
    assert "TOOL_RESULT read_file" in text
    assert "ERROR  bad thing" in text
    assert "SESSION  ended" in text


def test_close_is_idempotent(tmp_path):
    log = tmp_path / "session.log"
    console = AgentConsole(log_path=str(log))
    console.c, _ = quiet_console()
    console.close()
    console.close()  # second close must be a no-op, not raise
    assert log.read_text().count("SESSION  ended") == 1


def test_end_text_without_streaming_is_a_noop(tmp_path):
    """end_text() called when nothing has streamed shouldn't write an empty ASSISTANT entry."""
    log = tmp_path / "session.log"
    console = AgentConsole(log_path=str(log))
    console.c, _ = quiet_console()
    console.end_text()
    console.close()
    assert "ASSISTANT" not in log.read_text()


def test_multiline_body_is_indented_in_log(tmp_path):
    """_write replaces '\\n' with '\\n    ' so the log stays visually grouped."""
    log = tmp_path / "session.log"
    console = AgentConsole(log_path=str(log))
    console.c, _ = quiet_console()
    console.user_prompt("line1\nline2")
    console.close()
    assert "line1\n    line2" in log.read_text()
