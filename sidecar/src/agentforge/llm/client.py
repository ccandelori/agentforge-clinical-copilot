"""The LLMClient Protocol — the only surface the orchestrator depends on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from agentforge.llm.types import LLMResponse, Message, StreamEvent, ToolSpec


class LLMClient(Protocol):
    """Provider-agnostic async LLM interface.

    Two surfaces: ``complete()`` for one-shot requests and ``stream()``
    for incremental responses. The streaming variant exists to support
    the verifier's sentence-by-sentence safety check (Task #13) — the
    orchestrator buffers deltas, runs each completed sentence through
    the verifier, and only emits sentences that ground in the per-turn
    citation cache. Streaming unverified clinical text and "rewriting"
    it after would be a clinical-safety violation regardless of how
    fast the rewrite arrives.
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

    def stream(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[StreamEvent]:
        """Issue one streaming completion request.

        Yields :class:`StreamTextDelta` events as text arrives, then
        exactly one terminal :class:`StreamFinal` carrying the fully-
        assembled :class:`LLMResponse` (text, tool_calls, stop_reason,
        token counts). Tool calls are NOT streamed token-by-token —
        they're collected internally and surfaced on the final event.

        Note the signature uses ``def`` (not ``async def``): an async
        generator is a regular function whose return value is an
        ``AsyncIterator``. Implementations use ``async def`` + ``yield``
        which type-checks against this Protocol.
        """
        ...
