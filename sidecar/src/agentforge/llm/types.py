"""Provider-agnostic value types for the LLM client.

These types form the wire boundary between the orchestrator/agent code and
whichever concrete LLM provider is wired in. They are intentionally narrower
than the Anthropic SDK shapes so swapping in OpenAI / vLLM later is a
translation problem confined to the provider implementation, not a refactor
of every call site. See ARCHITECTURE.md §5.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A model-emitted request to invoke a tool.

    `id` is the provider-supplied correlator that must be echoed back on the
    matching tool-result `Message` so the model can pair them up.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    input: dict[str, Any]


class Message(BaseModel):
    """A single conversational turn in the provider-agnostic shape.

    - `role="user"` / `"assistant"`: ordinary text turns. Assistant messages
      that issued tool calls also carry `tool_calls`.
    - `role="tool"`: a tool's output being fed back to the model. Must carry
      `tool_call_id` matching the assistant's `ToolCall.id`. `content` holds
      the stringified tool result.
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class ToolSpec(BaseModel):
    """A tool offered to the model. `input_schema` is JSON Schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]


class LLMResponse(BaseModel):
    """The result of one `complete()` call, normalized across providers."""

    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str
    input_tokens: int
    output_tokens: int
