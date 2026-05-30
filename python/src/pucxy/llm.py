"""LLM client seam. See spec.md sections 3 and 4.6.

OpenAI-compatible (most providers expose this API). Kept behind this small seam
so swapping to a native client later is a one-file change. Streams text and
accumulates tool calls from deltas.
"""

from __future__ import annotations

import os
from typing import Any, Callable, cast

from .toolspec import TOOLS


class LLMError(Exception):
    """Network/SDK failure while talking to the LLM. Lets callers catch
    timeouts, drops, rate limits, etc. as one thing and keep the REPL alive."""


class LLMClient:
    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None):
        from openai import OpenAI  # lazy: importing this module must not require openai

        key = api_key or os.environ.get("PUCXY_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.client = OpenAI(base_url=base_url, api_key=key)
        self.model = model

    def respond(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
        tools: list[dict] = TOOLS,
    ) -> dict:
        """One assistant turn. Returns an OpenAI-shaped assistant message dict
        (with `tool_calls` when the model wants to act)."""
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=cast(Any, messages),  # our dicts vs the SDK's TypedDict params
                tools=cast(Any, tools),
                stream=True,
            )
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            calls: dict[int, dict] = {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                content = getattr(delta, "content", None)
                if content:
                    text_parts.append(content)
                    if on_text:
                        on_text(content)
                for tc in getattr(delta, "tool_calls", None) or []:
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
        except Exception as e:
            # Any SDK / httpx error during streaming — timeout, drop, rate limit — surfaces
            # here as the diverse openai+httpx exception zoo. Funnel them into LLMError so the
            # agent loop can keep the REPL alive on a single bad turn.
            raise LLMError(str(e)) from e
        msg: dict = {"role": "assistant", "content": "".join(text_parts) or None}
        if reasoning_parts:  # DeepSeek thinking mode requires this echoed back next turn
            msg["reasoning_content"] = "".join(reasoning_parts)
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return msg
