"""The LLMClient Protocol — the only surface the orchestrator depends on."""

from __future__ import annotations

from typing import Protocol

from agentforge.llm.types import LLMResponse, Message, ToolSpec


class LLMClient(Protocol):
    """Provider-agnostic async LLM interface.

    Kept deliberately small: a single `complete()` call that takes a system
    prompt, a message history, and an optional tool catalogue, and returns a
    normalized `LLMResponse`. Streaming and other niceties can be added once
    we have a second provider to pin the shape.
    """

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Issue one completion request and return the parsed response."""
        ...
