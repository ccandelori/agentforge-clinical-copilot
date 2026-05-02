"""Planner agent (Task 27).

The planner runs once per turn before the agent loop. It analyzes the
user message, picks a clinical use case from a closed taxonomy, and
emits a structured ``Plan`` (which tools to call + the parallel-batch
dispatch order). The agent loop consumes the plan to seed dispatch.

The four use cases are mutually exclusive — see ARCHITECTURE.md §3
for the taxonomy rationale and the per-UC tool selection guide.

This module ships the data model + consistency invariants. Subtasks
27.2 and 27.3 add tool-selection rules and the LLM-driven planner
itself.

Departure from spec: the original spec sketch had a
``planner_node(state: AgentState, llm)`` function shape that assumed
LangGraph state management. The codebase doesn't use LangGraph yet,
so we ship a ``Planner`` class with ``plan(user_message) -> Plan``
instead — closer to existing patterns (Orchestrator, Verifier) and
avoids a mid-task LangGraph adoption. Logged in DEVIATIONS.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UseCase(StrEnum):
    """The four canonical clinical-query use cases.

    Closed taxonomy — every user message is classified into exactly
    one. The values are the lowercase strings the LLM emits in its
    structured output.
    """

    ADMIT_SYNTHESIS = "admit_synthesis"
    CONTRAINDICATION = "contraindication"
    DELTA_COMPUTATION = "delta_computation"
    FOLLOWUP = "followup"


class PlannedToolCall(BaseModel):
    """One tool call the planner expects the orchestrator to issue."""

    model_config = ConfigDict(frozen=True)

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    """The planner's structured output.

    Two parallel views of the same dispatch intent:

      * ``tool_calls``  — flat tuple of named calls + their args
      * ``parallel_batches`` — sequence of batches; tools within a
        batch run concurrently, batches run sequentially.

    The two views must be consistent: every tool name in any batch
    appears in ``tool_calls`` (so the orchestrator knows the args),
    and every declared call appears in exactly one batch (so the
    orchestrator knows when to dispatch it). The empty plan
    ``Plan(use_case=..., tool_calls=(), parallel_batches=())`` is
    valid — the LLM can decide a message is purely conversational.
    """

    model_config = ConfigDict(frozen=True)

    use_case: UseCase
    tool_calls: tuple[PlannedToolCall, ...]
    parallel_batches: tuple[tuple[str, ...], ...]

    @model_validator(mode="after")
    def _check_consistency(self) -> Plan:
        named: set[str] = {tc.name for tc in self.tool_calls}
        seen_in_batches: set[str] = set()
        for batch in self.parallel_batches:
            for name in batch:
                if name not in named:
                    raise ValueError(
                        f"parallel_batches references unknown tool: {name!r}"
                    )
                if name in seen_in_batches:
                    raise ValueError(
                        f"tool {name!r} appears in multiple batches"
                    )
                seen_in_batches.add(name)
        for tc in self.tool_calls:
            if tc.name not in seen_in_batches:
                raise ValueError(
                    f"tool {tc.name!r} declared in tool_calls but not in any batch"
                )
        return self
