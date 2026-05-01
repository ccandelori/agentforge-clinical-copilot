"""Anthropic Claude implementation of the `LLMClient` Protocol.

The translation layer is the only place in the sidecar that knows about the
`anthropic` SDK's request/response shapes. Everything else speaks in the
provider-agnostic types from `agentforge.llm.types`.

Anthropic-specific quirks worth flagging:

  * The Messages API only accepts `role="user"` or `"assistant"` at the
    top level. Tool *results* are sent as a `user` message whose content is
    a list with a single `tool_result` block. Tool *calls* are emitted by
    assistant messages as `tool_use` content blocks. We collapse our flat
    `Message(role="tool", ...)` model down to that shape on the way out.
  * The SDK's `messages.create` returns a `Message` whose `content` is a
    heterogeneous list of `TextBlock` / `ToolUseBlock` / etc. We walk that
    list and pull out the pieces our `LLMResponse` cares about.
"""

from __future__ import annotations

from typing import Any, cast

from anthropic import AsyncAnthropic
from anthropic.types import (
    Message as AnthropicMessage,
)
from anthropic.types import (
    MessageParam,
    TextBlock,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
    ToolUseBlockParam,
)

from agentforge.llm.types import LLMResponse, Message, ToolCall, ToolSpec

DEFAULT_MODEL = "claude-sonnet-4-5"


class ClaudeClient:
    """`LLMClient` implementation backed by `anthropic.AsyncAnthropic`.

    The SDK client is injectable so tests can swap in an `AsyncMock` without
    touching the network. In production, leaving `http_client=None` causes
    the constructor to build its own `AsyncAnthropic` from `api_key`.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        http_client: AsyncAnthropic | None = None,
    ) -> None:
        self._model = model
        self._client: AsyncAnthropic = http_client or AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Issue one Messages API call and normalize the response."""
        api_messages = [self._to_anthropic_message(m) for m in messages]
        api_tools = [self._to_anthropic_tool(t) for t in (tools or [])]

        # The SDK accepts an empty `tools=[]` but the typing prefers omission;
        # passing the list either way is fine and keeps the call site simple.
        response: AnthropicMessage = await self._client.messages.create(
            model=self._model,
            system=system,
            messages=api_messages,
            tools=api_tools,
            max_tokens=max_tokens,
        )
        return self._from_anthropic_response(response)

    @staticmethod
    def _to_anthropic_message(message: Message) -> MessageParam:
        """Translate one of our flat `Message` objects into the SDK shape."""
        if message.role == "tool":
            # Tool results ride inside a `user` message as a tool_result block.
            if message.tool_call_id is None:
                raise ValueError("Message(role='tool') requires tool_call_id")
            tool_result: ToolResultBlockParam = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            return {"role": "user", "content": [tool_result]}

        if message.role == "assistant" and message.tool_calls:
            # Assistant turn that issued tool calls — emit text (if any) plus
            # one `tool_use` block per call so the model sees its own history.
            blocks: list[TextBlockParam | ToolUseBlockParam] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": dict(call.input),
                    }
                )
            return {"role": "assistant", "content": blocks}

        # Plain user / assistant text turn. `role="tool"` is excluded above,
        # but mypy can't narrow the Literal through the early returns, so we
        # spell out the cases instead of casting.
        if message.role == "user":
            return {"role": "user", "content": message.content}
        return {"role": "assistant", "content": message.content}

    @staticmethod
    def _to_anthropic_tool(tool: ToolSpec) -> ToolParam:
        # `input_schema` is JSON Schema; the SDK's TypedDict declares it with
        # tighter shape than `dict[str, Any]`, so a cast is the cleanest path.
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": cast(Any, tool.input_schema),
        }

    @staticmethod
    def _from_anthropic_response(response: AnthropicMessage) -> LLMResponse:
        """Walk the response content blocks into our normalized shape."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                # SDK types `input` as `object`; the API contract is JSON object,
                # so a runtime check keeps mypy honest without a blanket cast.
                raw_input = block.input
                if not isinstance(raw_input, dict):
                    raise TypeError(
                        f"Expected dict for tool_use input, got {type(raw_input).__name__}"
                    )
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(raw_input)))
            # Other block types (thinking, server tool results, etc.) are
            # ignored for MVP — add explicit handling when we wire them up.

        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "unknown",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
