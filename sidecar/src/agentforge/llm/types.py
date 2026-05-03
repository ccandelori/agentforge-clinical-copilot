"""Provider-agnostic value types for the LLM client.

These types form the wire boundary between the orchestrator/agent code and
whichever concrete LLM provider is wired in. They are intentionally narrower
than the Anthropic SDK shapes so swapping in OpenAI / vLLM later is a
translation problem confined to the provider implementation, not a refactor
of every call site. See ARCHITECTURE.md §5.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

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


class StreamTextDelta(BaseModel):
    """Incremental text emitted by a streaming LLM call.

    Each delta carries only the new characters since the last delta —
    consumers concatenate them to reconstruct the full response. The
    SDK guarantees deltas arrive in order; we don't re-order or
    deduplicate at this layer.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["text_delta"] = "text_delta"
    text: str


class StreamFinal(BaseModel):
    """Terminal event from a streaming LLM call.

    Always the LAST event yielded by ``LLMClient.stream``. Carries the
    fully-assembled :class:`LLMResponse` so callers can read
    ``tool_calls``, ``stop_reason``, and token counts after the text
    deltas have all arrived. The verifier (Task #13) gates on this
    event before emitting any text — streaming unverified clinical
    content would be a clinical-safety violation.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["final"] = "final"
    response: LLMResponse


# Discriminated union over the two concrete event types. Pydantic's
# `discriminator="kind"` makes round-tripping through model_validate
# select the right subclass without an explicit isinstance dance.
# Internal consumers pattern-match on `event.kind` or `isinstance`.
StreamEvent = Annotated[
    StreamTextDelta | StreamFinal,
    Field(discriminator="kind"),
]
