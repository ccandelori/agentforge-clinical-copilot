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

import logging
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message, ToolSpec

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# 27.2 — tool selection rules per use case
# ---------------------------------------------------------------------------


# Maximum tools per parallel dispatch batch. Synthea-shape responses
# stream back in tens of milliseconds each, so a single batch with all
# eight catalogue tools would work — but we cap at 4 to avoid spiking
# the dev DB or hitting rate-limits when the orchestrator gets wired
# in. Adjust if a per-tool fetcher genuinely needs to go solo.
DEFAULT_BATCH_SIZE: int = 4


# Per-use-case default tool selection. Hand-authored to match the
# clinical intent of each use case; the LLM-driven planner (27.3) uses
# this as a hint baked into its prompt, and the orchestrator falls
# back to ``default_plan_for(uc)`` if the LLM's structured output is
# unparseable. FOLLOWUP intentionally maps to () — pure follow-ups
# depend on conversation context, not on an a-priori tool set.
TOOL_SELECTION_BY_USE_CASE: dict[UseCase, tuple[str, ...]] = {
    UseCase.ADMIT_SYNTHESIS: (
        "get_demographics",
        "get_active_problems",
        "get_active_medications",
        "get_active_allergies",
        "get_recent_labs",
        "get_vitals_trend",
        "get_recent_encounters",
        "get_immunizations",
        "get_procedures",
        "get_recent_notes",
    ),
    UseCase.CONTRAINDICATION: (
        "get_active_problems",
        "get_active_medications",
        "get_active_allergies",
    ),
    UseCase.DELTA_COMPUTATION: (
        "get_recent_encounters",
        "get_active_problems",
        "get_active_medications",
        "get_recent_labs",
        "get_procedures",
        "get_recent_notes",
    ),
    UseCase.FOLLOWUP: (),
}


def default_plan_for(use_case: UseCase) -> Plan:
    """Build a default ``Plan`` for ``use_case`` from the static rules.

    The orchestrator uses this as the LLM-bypass fallback when the
    planner's structured output is unparseable. Tools are grouped into
    parallel batches of up to ``DEFAULT_BATCH_SIZE``; within a batch
    they run concurrently, batches run sequentially.
    """
    tool_names = TOOL_SELECTION_BY_USE_CASE[use_case]
    tool_calls = tuple(PlannedToolCall(name=n) for n in tool_names)
    batches: list[tuple[str, ...]] = []
    for i in range(0, len(tool_names), DEFAULT_BATCH_SIZE):
        batches.append(tool_names[i : i + DEFAULT_BATCH_SIZE])
    return Plan(
        use_case=use_case,
        tool_calls=tool_calls,
        parallel_batches=tuple(batches),
    )


# ---------------------------------------------------------------------------
# 27.3 — LLM-driven planner with tool-use forced JSON output
# ---------------------------------------------------------------------------


PLANNER_SYSTEM_PROMPT = """\
You are a clinical query planner for an EHR co-pilot. Given a clinician's \
message about an active patient, classify the message into exactly one use \
case and emit a structured tool dispatch plan.

Use cases (mutually exclusive):
- admit_synthesis: "summarize this chart", "what do I need to know", broad \
chart review.
- contraindication: "is there a contraindication", "can I give X", \
"any interactions" — safety check before prescribing.
- delta_computation: "what changed since last visit", "is this new" — \
temporal comparison.
- followup: short follow-up to a previous question that doesn't require \
new chart data; reuse the prior turn's results.

Tool catalogue: get_demographics, get_active_problems, \
get_active_medications, get_active_allergies, get_recent_labs, \
get_vitals_trend, get_recent_encounters, get_recent_notes, search_notes.

Default tool selections per use case (you MAY adjust based on context):
- admit_synthesis: demographics + problems + medications + allergies + \
labs + vitals + encounters + notes
- contraindication: problems + medications + allergies (the safety triad)
- delta_computation: encounters + problems + medications + labs + notes
- followup: usually no tools

Group selected tools into parallel_batches: a list of lists. Tools within \
a batch run concurrently; batches run sequentially. Cap each batch at 4 \
tools. Every tool name in any batch MUST also appear in tool_calls (so \
the orchestrator knows the args), and every tool_call MUST appear in \
exactly one batch.

You MUST call the submit_plan tool with your decision. Do not respond in \
free text.\
"""


SUBMIT_PLAN_TOOL_SPEC = ToolSpec(
    name="submit_plan",
    description=(
        "Submit the structured planning decision for the user's clinical "
        "query. The orchestrator dispatches the parallel_batches in order "
        "and feeds the results to the synthesis step."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "use_case": {
                "type": "string",
                "enum": [uc.value for uc in UseCase],
                "description": "The classified use case for this query.",
            },
            "tool_calls": {
                "type": "array",
                "description": (
                    "Flat list of tools to invoke this turn, with their "
                    "named args. Every tool name here must appear in "
                    "exactly one batch below."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "args": {
                            "type": "object",
                            "description": (
                                "Optional named args; defaults to {}."
                            ),
                        },
                    },
                    "required": ["name"],
                },
            },
            "parallel_batches": {
                "type": "array",
                "description": (
                    "Sequence of dispatch batches. Tools within a batch "
                    "run concurrently; batches run sequentially. Cap at "
                    "4 tools per batch."
                ),
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "required": ["use_case", "tool_calls", "parallel_batches"],
    },
)


class Planner:
    """LLM-driven query planner.

    Calls the model once with the planner system prompt and a single
    ``submit_plan`` tool. The model is instructed to emit its
    classification + dispatch plan via that tool only; we read the
    structured input directly off the tool_use block.

    Failure modes — both fall back to ``default_plan_for(ADMIT_SYNTHESIS)``
    rather than failing the turn:

      * No ``submit_plan`` tool call in the response (model emitted
        text-only).
      * ``submit_plan`` input fails ``Plan`` validation (e.g.
        parallel_batches references undeclared tools).

    Network / SDK errors propagate — the orchestrator's retry policy is
    the right place to handle those.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def plan(self, user_message: str) -> Plan:
        response = await self._llm.complete(
            system=PLANNER_SYSTEM_PROMPT,
            messages=[Message(role="user", content=user_message)],
            tools=[SUBMIT_PLAN_TOOL_SPEC],
            max_tokens=1024,
        )
        for call in response.tool_calls:
            if call.name != "submit_plan":
                continue
            try:
                return Plan.model_validate(call.input)
            except ValidationError as exc:
                logger.warning(
                    "submit_plan input failed Plan validation; falling back",
                    extra={"validation_error": str(exc)},
                )
                return default_plan_for(UseCase.ADMIT_SYNTHESIS)

        logger.warning(
            "planner LLM returned no submit_plan tool call; falling back",
        )
        return default_plan_for(UseCase.ADMIT_SYNTHESIS)
