"""Tests for the Claude LLM client translation layer.

We mock `anthropic.AsyncAnthropic.messages.create` so the tests never touch
the network. The goal is to verify the request/response translation —
not the SDK or the wire protocol, which Anthropic owns.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

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

from agentforge.llm import ClaudeClient, Message, ToolCall


def _fake_anthropic(response: AnthropicMessage) -> AsyncAnthropic:
    """Build a stand-in AsyncAnthropic whose messages.create returns `response`.

    Returned object is a `MagicMock` with `.messages.create` patched as an
    `AsyncMock`. We cast it to `AsyncAnthropic` so the `ClaudeClient`
    constructor accepts it; the runtime mock honours the same call surface.
    """
    fake = MagicMock()
    fake.messages.create = AsyncMock(return_value=response)
    return cast(AsyncAnthropic, fake)


def _make_response(
    *,
    content: list[Any],
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> AnthropicMessage:
    """Build an `anthropic.types.Message` response object for tests."""
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
async def test_simple_text_completion_returns_normalized_response() -> None:
    text_block = TextBlock.model_construct(type="text", text="Hello back!", citations=None)
    fake_sdk = _fake_anthropic(_make_response(content=[text_block]))
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    result = await client.complete(
        system="You are a test fixture.",
        messages=[Message(role="user", content="Hello?")],
    )

    assert result.text == "Hello back!"
    assert result.tool_calls == []
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 10
    assert result.output_tokens == 5

    # Verify the request shape we sent up to the SDK.
    create = cast(AsyncMock, fake_sdk.messages.create)
    create.assert_awaited_once()
    await_args = create.await_args
    assert await_args is not None
    sent = await_args.kwargs
    assert sent["system"] == "You are a test fixture."
    assert sent["messages"] == [{"role": "user", "content": "Hello?"}]
    assert sent["tools"] == []


@pytest.mark.asyncio
async def test_tool_use_response_is_parsed_into_tool_call() -> None:
    tool_use = ToolUseBlock.model_construct(
        type="tool_use",
        id="toolu_abc123",
        name="get_patient",
        input={"patient_id": "42"},
    )
    fake_sdk = _fake_anthropic(
        _make_response(content=[tool_use], stop_reason="tool_use"),
    )
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    result = await client.complete(system="sys", messages=[Message(role="user", content="go")])

    assert result.stop_reason == "tool_use"
    assert result.text == ""
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "toolu_abc123"
    assert call.name == "get_patient"
    assert call.input == {"patient_id": "42"}


@pytest.mark.asyncio
async def test_tool_result_message_translates_to_user_tool_result_block() -> None:
    text_block = TextBlock.model_construct(type="text", text="ack", citations=None)
    fake_sdk = _fake_anthropic(_make_response(content=[text_block]))
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    history = [
        Message(role="user", content="lookup patient 42"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="toolu_abc123", name="get_patient", input={"id": "42"})],
        ),
        Message(role="tool", tool_call_id="toolu_abc123", content='{"name": "Jane Doe"}'),
    ]

    await client.complete(system="sys", messages=history)

    create = cast(AsyncMock, fake_sdk.messages.create)
    await_args = create.await_args
    assert await_args is not None
    sent_messages = await_args.kwargs["messages"]
    assert sent_messages[0] == {"role": "user", "content": "lookup patient 42"}
    # Assistant turn carrying the tool_use block.
    assert sent_messages[1] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_abc123",
                "name": "get_patient",
                "input": {"id": "42"},
            }
        ],
    }
    # Tool result collapsed into a user message with a tool_result block.
    assert sent_messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_abc123",
                "content": '{"name": "Jane Doe"}',
            }
        ],
    }


@pytest.mark.asyncio
async def test_health_check_issues_minimal_completion() -> None:
    """ClaudeClient.health_check pings the API with a min-cost request.

    The point is liveness-with-auth: a tiny `messages.create` call is
    the cheapest way to confirm the API is reachable and the API key
    still works. Cost is bounded by ``max_tokens=1`` plus a one-token
    user message — see the comment in ClaudeClient for the rationale.
    """
    text_block = TextBlock.model_construct(type="text", text="ok", citations=None)
    fake_sdk = _fake_anthropic(_make_response(content=[text_block]))
    client = ClaudeClient(api_key="unused", http_client=fake_sdk)

    await client.health_check()

    create = cast(AsyncMock, fake_sdk.messages.create)
    create.assert_awaited_once()
    sent = create.await_args.kwargs  # type: ignore[union-attr]
    assert sent["max_tokens"] == 1
    # The probe must NOT carry a tool catalogue — health checks should
    # never resemble real turns to the auth/rate-limit infrastructure.
    assert sent.get("tools") in (None, [])


@pytest.mark.asyncio
async def test_health_check_propagates_sdk_errors() -> None:
    """A failing API call must raise — the monitor counts it as a failure."""
    fake_sdk = MagicMock()
    fake_sdk.messages.create = AsyncMock(side_effect=RuntimeError("503"))
    client = ClaudeClient(api_key="unused", http_client=cast(AsyncAnthropic, fake_sdk))

    with pytest.raises(RuntimeError):
        await client.health_check()
