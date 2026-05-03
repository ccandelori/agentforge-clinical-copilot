"""Tests for the ClaudeClient streaming surface (week1-gaps Task #9).

We mock ``anthropic.AsyncAnthropic.messages.stream`` so the tests never
touch the network. The goal is the same as ``test_llm.py``: verify the
request/response translation, not the SDK or the wire protocol.

The SDK shape we mock:

  * ``client.messages.stream(...)`` returns an *async context manager*.
  * ``async with`` enters and yields a stream handle.
  * ``stream_handle.text_stream`` is an ``AsyncIterator[str]`` of deltas.
  * ``await stream_handle.get_final_message()`` returns the assembled
    ``anthropic.types.Message`` once the deltas are exhausted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from anthropic import AsyncAnthropic
from anthropic.types import (
    Message as AnthropicMessage,
)
from anthropic.types import (
    TextBlock,
    ToolUseBlock,
    Usage,
)

from agentforge.llm import ClaudeClient, Message, StreamFinal, StreamTextDelta


class _FakeStreamHandle:
    """Stand-in for ``anthropic.lib.streaming.AsyncMessageStream``.

    Implements the two surfaces the ClaudeClient streaming code uses:
    the ``text_stream`` async iterator and the ``get_final_message``
    coroutine.
    """

    def __init__(
        self,
        deltas: list[str],
        final: AnthropicMessage,
    ) -> None:
        self._deltas = deltas
        self._final = final

    async def __aenter__(self) -> _FakeStreamHandle:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def _iter() -> AsyncIterator[str]:
            for d in self._deltas:
                yield d

        return _iter()

    async def get_final_message(self) -> AnthropicMessage:
        return self._final


def _fake_anthropic_stream(
    deltas: list[str], final: AnthropicMessage
) -> AsyncAnthropic:
    """Build a stand-in AsyncAnthropic whose messages.stream() returns
    a ``_FakeStreamHandle``. Note: ``messages.stream`` is a regular
    sync callable that returns the async context manager — it is
    NOT an ``AsyncMock``.
    """
    fake = MagicMock()
    fake.messages.stream = MagicMock(
        return_value=_FakeStreamHandle(deltas, final)
    )
    return cast(AsyncAnthropic, fake)


def _make_final(
    *,
    content: list[Any],
    stop_reason: str = "end_turn",
    input_tokens: int = 12,
    output_tokens: int = 7,
) -> AnthropicMessage:
    return AnthropicMessage.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-5",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage.model_construct(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_stream_yields_text_deltas_in_order_then_final() -> None:
    deltas = ["Hello, ", "Susan! ", "Your last visit was..."]
    final_text = "".join(deltas)
    text_block = TextBlock.model_construct(
        type="text", text=final_text, citations=None
    )
    fake_sdk = _fake_anthropic_stream(
        deltas, _make_final(content=[text_block])
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    events: list[StreamTextDelta | StreamFinal] = [
        event async for event in client.stream(
            system="sys", messages=[Message(role="user", content="hi")]
        )
    ]

    # First three events are deltas in order; last event is the final.
    assert len(events) == 4
    assert all(isinstance(e, StreamTextDelta) for e in events[:3])
    assert [
        cast(StreamTextDelta, e).text for e in events[:3]
    ] == deltas
    final = events[3]
    assert isinstance(final, StreamFinal)
    assert final.response.text == final_text


@pytest.mark.asyncio
async def test_stream_final_carries_tool_calls() -> None:
    # Tool calls aren't streamed token-by-token — the SDK assembles
    # them and we surface them on the final event so the verifier sees
    # the complete tool_calls list.
    tool_use = ToolUseBlock.model_construct(
        type="tool_use",
        id="toolu_abc123",
        name="get_demographics",
        input={},
    )
    fake_sdk = _fake_anthropic_stream(
        deltas=[],
        final=_make_final(content=[tool_use], stop_reason="tool_use"),
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    events = [
        e async for e in client.stream(
            system="sys", messages=[Message(role="user", content="who is this?")]
        )
    ]

    # No deltas — just the final.
    assert len(events) == 1
    final = events[0]
    assert isinstance(final, StreamFinal)
    assert final.response.stop_reason == "tool_use"
    assert len(final.response.tool_calls) == 1
    call = final.response.tool_calls[0]
    assert call.id == "toolu_abc123"
    assert call.name == "get_demographics"


@pytest.mark.asyncio
async def test_stream_final_carries_token_counts() -> None:
    text_block = TextBlock.model_construct(
        type="text", text="ok", citations=None
    )
    fake_sdk = _fake_anthropic_stream(
        deltas=["ok"],
        final=_make_final(
            content=[text_block], input_tokens=42, output_tokens=3
        ),
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    events = [
        e async for e in client.stream(
            system="sys", messages=[Message(role="user", content="hi")]
        )
    ]

    final = events[-1]
    assert isinstance(final, StreamFinal)
    assert final.response.input_tokens == 42
    assert final.response.output_tokens == 3


@pytest.mark.asyncio
async def test_stream_passes_request_shape_through_to_sdk() -> None:
    text_block = TextBlock.model_construct(
        type="text", text="ack", citations=None
    )
    fake_sdk = _fake_anthropic_stream(
        deltas=["ack"], final=_make_final(content=[text_block])
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    [
        e async for e in client.stream(
            system="You are a test fixture.",
            messages=[Message(role="user", content="Hello?")],
        )
    ]

    stream_call = cast(MagicMock, fake_sdk.messages.stream)
    stream_call.assert_called_once()
    sent = stream_call.call_args.kwargs
    assert sent["system"] == "You are a test fixture."
    assert sent["messages"] == [{"role": "user", "content": "Hello?"}]
    assert sent["tools"] == []
    assert sent["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_stream_with_no_text_still_yields_final() -> None:
    # Edge case: the model decides to ONLY tool-use without any text.
    # We must still emit the final event so callers can read
    # tool_calls + stop_reason — otherwise the orchestrator's loop
    # would have no signal that the stream completed.
    tool_use = ToolUseBlock.model_construct(
        type="tool_use", id="t1", name="get_demographics", input={}
    )
    fake_sdk = _fake_anthropic_stream(
        deltas=[],
        final=_make_final(content=[tool_use], stop_reason="tool_use"),
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    events = [
        e async for e in client.stream(
            system="sys", messages=[Message(role="user", content="hi")]
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], StreamFinal)
