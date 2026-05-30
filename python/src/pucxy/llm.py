"""LLM client seam. See spec.md sections 3 and 4.6.

OpenAI-compatible (most providers expose this API). Kept behind this small seam
so swapping to a native client later is a one-file change. Streams text and
accumulates tool calls from deltas.
"""

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, cast

from openai import OpenAI

from .toolspec import TOOLS


class LLMError(Exception):
    """Network/SDK failure while talking to the LLM. Lets callers catch
    timeouts, drops, rate limits, etc. as one thing and keep the REPL alive."""


@dataclass
class LLM:
    client: OpenAI
    model: str


def make_llm(model: str, base_url: str | None = None, api_key: str | None = None) -> LLM:
    """Build an LLM bound to the given model. Picks the API key from arg or env."""
    key = api_key or os.environ.get("PUCXY_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return LLM(OpenAI(base_url=base_url, api_key=key), model)


def respond(
    llm: LLM,
    messages: list[dict],
    on_text: Callable[[str], None] | None = None,
    tools: list[dict] = TOOLS,
) -> dict:
    """One assistant turn. Returns an OpenAI-shaped assistant message dict
    (with `tool_calls` when the model wants to act)."""
    try:
        stream = llm.client.chat.completions.create(
            model=llm.model,
            messages=cast(Any, messages),  # our dicts vs the SDK's TypedDict params
            tools=cast(Any, tools),
            stream=True,
        )
        accum = _accumulate_stream(stream, on_text)
    except Exception as e:
        # Any SDK / httpx error during streaming — timeout, drop, rate limit — surfaces
        # here as the diverse openai+httpx exception zoo. Funnel them into LLMError so the
        # agent loop can keep the REPL alive on a single bad turn.
        raise LLMError(str(e)) from e
    return _assistant_message(accum)


@dataclass
class _StreamAccum:
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict] = field(default_factory=dict)


def _accumulate_stream(stream: Iterable, on_text: Callable[[str], None] | None) -> _StreamAccum:
    accum = _StreamAccum()
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            accum.reasoning.append(reasoning)
        content = getattr(delta, "content", None)
        if content:
            accum.text.append(content)
            if on_text:
                on_text(content)
        for tc in getattr(delta, "tool_calls", None) or []:
            _merge_tool_call_delta(accum.tool_calls, tc)
    return accum


def _merge_tool_call_delta(calls: dict[int, dict], tc: Any) -> None:
    slot = calls.setdefault(
        tc.index,
        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if tc.id:
        slot["id"] = tc.id
    if tc.function and tc.function.name:
        slot["function"]["name"] += tc.function.name
    if tc.function and tc.function.arguments:
        slot["function"]["arguments"] += tc.function.arguments


def _assistant_message(accum: _StreamAccum) -> dict:
    msg: dict = {"role": "assistant", "content": "".join(accum.text) or None}
    if accum.reasoning:  # DeepSeek thinking mode requires this echoed back next turn
        msg["reasoning_content"] = "".join(accum.reasoning)
    if accum.tool_calls:
        msg["tool_calls"] = [accum.tool_calls[i] for i in sorted(accum.tool_calls)]
    return msg
