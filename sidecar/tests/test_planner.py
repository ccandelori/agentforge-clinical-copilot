"""Planner agent unit tests (Task 27).

The planner runs once per turn before the agent loop. It analyzes the
user message, picks a clinical use case from a closed taxonomy, and
emits a structured `Plan` (which tools to call + the parallel-batch
dispatch order). The agent loop consumes the plan to seed dispatch.

Tests are split by subtask:
  27.1 — taxonomy + Plan data structure (this file).
  27.2 — tool selection rules per use case.
  27.3 — LLM-driven planner with tool-use forced JSON output.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.orchestrator.planner import (
    DEFAULT_BATCH_SIZE,
    TOOL_SELECTION_BY_USE_CASE,
    Plan,
    PlannedToolCall,
    UseCase,
    default_plan_for,
)

# ---------------------------------------------------------------------------
# Subtask 27.1 — taxonomy + Plan data structure
# ---------------------------------------------------------------------------


class TestUseCaseTaxonomy:
    def test_four_canonical_use_cases_exist(self) -> None:
        # Exactly four use cases per ARCHITECTURE.md §3 / Task 27 spec.
        names = {uc.name for uc in UseCase}
        assert names == {
            "ADMIT_SYNTHESIS",
            "CONTRAINDICATION",
            "DELTA_COMPUTATION",
            "FOLLOWUP",
        }

    def test_use_case_value_matches_spec(self) -> None:
        # Spec defines the lowercase string values used in JSON output.
        assert UseCase.ADMIT_SYNTHESIS.value == "admit_synthesis"
        assert UseCase.CONTRAINDICATION.value == "contraindication"
        assert UseCase.DELTA_COMPUTATION.value == "delta_computation"
        assert UseCase.FOLLOWUP.value == "followup"


class TestPlannedToolCall:
    def test_constructs_with_name_and_args(self) -> None:
        tc = PlannedToolCall(name="get_demographics", args={"foo": 1})
        assert tc.name == "get_demographics"
        assert tc.args == {"foo": 1}

    def test_args_default_to_empty_dict(self) -> None:
        tc = PlannedToolCall(name="get_demographics")
        assert tc.args == {}

    def test_is_frozen(self) -> None:
        tc = PlannedToolCall(name="get_demographics")
        with pytest.raises(ValidationError):
            tc.name = "other"  # type: ignore[misc]


class TestPlan:
    def test_round_trip_simple_plan(self) -> None:
        plan = Plan(
            use_case=UseCase.ADMIT_SYNTHESIS,
            tool_calls=(
                PlannedToolCall(name="get_demographics"),
                PlannedToolCall(name="get_active_problems"),
            ),
            parallel_batches=(
                ("get_demographics",),
                ("get_active_problems",),
            ),
        )
        assert plan.use_case == UseCase.ADMIT_SYNTHESIS
        assert len(plan.tool_calls) == 2
        assert len(plan.parallel_batches) == 2

    def test_rejects_batch_referencing_unknown_tool(self) -> None:
        with pytest.raises(ValidationError, match="unknown tool"):
            Plan(
                use_case=UseCase.ADMIT_SYNTHESIS,
                tool_calls=(PlannedToolCall(name="get_demographics"),),
                parallel_batches=(
                    ("get_demographics",),
                    ("get_recent_notes",),  # not declared
                ),
            )

    def test_rejects_tool_appearing_in_multiple_batches(self) -> None:
        with pytest.raises(ValidationError, match="multiple batches"):
            Plan(
                use_case=UseCase.ADMIT_SYNTHESIS,
                tool_calls=(PlannedToolCall(name="get_demographics"),),
                parallel_batches=(
                    ("get_demographics",),
                    ("get_demographics",),
                ),
            )

    def test_rejects_declared_tool_not_in_any_batch(self) -> None:
        with pytest.raises(ValidationError, match="not in any batch"):
            Plan(
                use_case=UseCase.ADMIT_SYNTHESIS,
                tool_calls=(
                    PlannedToolCall(name="get_demographics"),
                    PlannedToolCall(name="get_recent_notes"),
                ),
                parallel_batches=(("get_demographics",),),  # missing notes
            )

    def test_empty_plan_with_no_tools_is_valid(self) -> None:
        # A plan that picks no tools (the LLM might decide a question
        # is purely conversational) is valid — empty batches, empty
        # tool_calls.
        plan = Plan(
            use_case=UseCase.FOLLOWUP,
            tool_calls=(),
            parallel_batches=(),
        )
        assert plan.tool_calls == ()
        assert plan.parallel_batches == ()

    def test_is_frozen(self) -> None:
        plan = Plan(
            use_case=UseCase.ADMIT_SYNTHESIS,
            tool_calls=(),
            parallel_batches=(),
        )
        with pytest.raises(ValidationError):
            plan.use_case = UseCase.FOLLOWUP  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Subtask 27.2 — tool selection rules per use case
# ---------------------------------------------------------------------------


class TestToolSelectionRules:
    def test_every_use_case_has_an_entry(self) -> None:
        # Including FOLLOWUP, which legitimately maps to no tools.
        assert set(TOOL_SELECTION_BY_USE_CASE.keys()) == set(UseCase)

    def test_admit_synthesis_picks_broad_chart_summary_tools(self) -> None:
        tools = TOOL_SELECTION_BY_USE_CASE[UseCase.ADMIT_SYNTHESIS]
        # The summary use case should hit demographics + the four core
        # clinical lists + the recency-aware tools.
        for required in (
            "get_demographics",
            "get_active_problems",
            "get_active_medications",
            "get_active_allergies",
            "get_recent_labs",
            "get_recent_encounters",
            "get_recent_notes",
        ):
            assert required in tools, f"admit_synthesis missing {required}"

    def test_contraindication_picks_safety_triad(self) -> None:
        # The "can I give X / interactions" question needs problems
        # (comorbidities), medications (current regimen), allergies.
        tools = TOOL_SELECTION_BY_USE_CASE[UseCase.CONTRAINDICATION]
        assert set(tools) >= {
            "get_active_problems",
            "get_active_medications",
            "get_active_allergies",
        }

    def test_delta_computation_includes_encounters_and_recent_lists(
        self,
    ) -> None:
        # "What changed since last visit" needs the encounter timeline
        # to anchor "since when" and the recency-aware lists to compare.
        tools = TOOL_SELECTION_BY_USE_CASE[UseCase.DELTA_COMPUTATION]
        assert "get_recent_encounters" in tools
        assert any(t.startswith("get_recent_") for t in tools)

    def test_followup_default_is_empty(self) -> None:
        # Pure follow-ups depend on conversation context — no default
        # tool fan-out. The orchestrator will reuse the prior turn's
        # cached results; the LLM decides whether new tools are needed.
        assert TOOL_SELECTION_BY_USE_CASE[UseCase.FOLLOWUP] == ()


class TestDefaultPlanFor:
    def test_returns_valid_plan_for_admit_synthesis(self) -> None:
        plan = default_plan_for(UseCase.ADMIT_SYNTHESIS)
        assert plan.use_case == UseCase.ADMIT_SYNTHESIS
        assert len(plan.tool_calls) == len(
            TOOL_SELECTION_BY_USE_CASE[UseCase.ADMIT_SYNTHESIS]
        )
        # Plan validation already enforces bijection batches <-> tool_calls;
        # we just confirm at least one batch was emitted.
        assert len(plan.parallel_batches) >= 1

    def test_default_batches_respect_default_batch_size(self) -> None:
        plan = default_plan_for(UseCase.ADMIT_SYNTHESIS)
        for batch in plan.parallel_batches:
            assert 0 < len(batch) <= DEFAULT_BATCH_SIZE

    def test_default_plan_for_followup_is_empty(self) -> None:
        plan = default_plan_for(UseCase.FOLLOWUP)
        assert plan.tool_calls == ()
        assert plan.parallel_batches == ()

    def test_default_plan_includes_every_default_tool(self) -> None:
        for uc in UseCase:
            plan = default_plan_for(uc)
            expected = set(TOOL_SELECTION_BY_USE_CASE[uc])
            actual = {tc.name for tc in plan.tool_calls}
            assert actual == expected, f"mismatch for {uc.name}"


# ---------------------------------------------------------------------------
# Subtask 27.3 — LLM-driven planner with structured tool-use output
# ---------------------------------------------------------------------------


class TestSubmitPlanToolSpec:
    def test_spec_describes_a_well_formed_tool(self) -> None:
        from agentforge.orchestrator.planner import SUBMIT_PLAN_TOOL_SPEC

        assert SUBMIT_PLAN_TOOL_SPEC.name == "submit_plan"
        # Schema must declare use_case as a closed enum and list the
        # other two fields. The orchestrator and the LLM both rely on
        # this surface.
        schema = SUBMIT_PLAN_TOOL_SPEC.input_schema
        assert schema["type"] == "object"
        assert "use_case" in schema["required"]
        assert "tool_calls" in schema["required"]
        assert "parallel_batches" in schema["required"]
        use_case_enum = schema["properties"]["use_case"]["enum"]
        assert set(use_case_enum) == {uc.value for uc in UseCase}


class TestPlanner:
    @pytest.mark.asyncio
    async def test_returns_parsed_plan_when_llm_emits_submit_plan_call(
        self,
    ) -> None:
        from unittest.mock import AsyncMock

        from agentforge.llm.types import LLMResponse, ToolCall
        from agentforge.orchestrator.planner import Planner

        canned_plan_input = {
            "use_case": "contraindication",
            "tool_calls": [
                {"name": "get_active_problems", "args": {}},
                {"name": "get_active_medications", "args": {}},
                {"name": "get_active_allergies", "args": {}},
            ],
            "parallel_batches": [
                ["get_active_problems", "get_active_medications", "get_active_allergies"],
            ],
        }

        llm = AsyncMock()
        llm.complete.return_value = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="tu_1", name="submit_plan", input=canned_plan_input)
            ],
            stop_reason="tool_use",
            input_tokens=120,
            output_tokens=42,
        )

        planner = Planner(llm)
        plan = await planner.plan("Can I prescribe metformin for this patient?")

        assert plan.use_case == UseCase.CONTRAINDICATION
        names = {tc.name for tc in plan.tool_calls}
        assert names == {
            "get_active_problems",
            "get_active_medications",
            "get_active_allergies",
        }

    @pytest.mark.asyncio
    async def test_falls_back_to_admit_synthesis_when_no_tool_call(self) -> None:
        from unittest.mock import AsyncMock

        from agentforge.llm.types import LLMResponse
        from agentforge.orchestrator.planner import Planner

        llm = AsyncMock()
        llm.complete.return_value = LLMResponse(
            text="(model went text-only instead of using the tool)",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=120,
            output_tokens=20,
        )

        planner = Planner(llm)
        plan = await planner.plan("Summarize this patient.")

        # The fallback is the broadest UC so the orchestrator still
        # gets useful context even when the planner punted.
        assert plan.use_case == UseCase.ADMIT_SYNTHESIS
        assert len(plan.tool_calls) == len(
            TOOL_SELECTION_BY_USE_CASE[UseCase.ADMIT_SYNTHESIS]
        )

    @pytest.mark.asyncio
    async def test_falls_back_when_plan_input_is_invalid(self) -> None:
        from unittest.mock import AsyncMock

        from agentforge.llm.types import LLMResponse, ToolCall
        from agentforge.orchestrator.planner import Planner

        llm = AsyncMock()
        # parallel_batches references a tool not in tool_calls — Plan
        # validation will reject it. Planner should swallow the error
        # and fall back instead of crashing the turn.
        bad_input = {
            "use_case": "admit_synthesis",
            "tool_calls": [{"name": "get_demographics", "args": {}}],
            "parallel_batches": [["get_demographics", "get_recent_notes"]],
        }
        llm.complete.return_value = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(id="tu_1", name="submit_plan", input=bad_input)
            ],
            stop_reason="tool_use",
            input_tokens=120,
            output_tokens=42,
        )

        planner = Planner(llm)
        plan = await planner.plan("Summarize this patient.")
        assert plan.use_case == UseCase.ADMIT_SYNTHESIS
        # Invariant: fallback returns a fresh default plan, not the
        # mangled input.
        assert len(plan.tool_calls) == len(
            TOOL_SELECTION_BY_USE_CASE[UseCase.ADMIT_SYNTHESIS]
        )

    @pytest.mark.asyncio
    async def test_passes_user_message_through_to_llm(self) -> None:
        from unittest.mock import AsyncMock

        from agentforge.llm.types import LLMResponse, ToolCall
        from agentforge.orchestrator.planner import Planner

        llm = AsyncMock()
        llm.complete.return_value = LLMResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id="tu_1",
                    name="submit_plan",
                    input={
                        "use_case": "followup",
                        "tool_calls": [],
                        "parallel_batches": [],
                    },
                )
            ],
            stop_reason="tool_use",
            input_tokens=120,
            output_tokens=12,
        )

        planner = Planner(llm)
        await planner.plan("Tell me more.")

        # The single user message should be on the messages list with
        # exact content.
        kwargs = llm.complete.await_args.kwargs
        assert any(
            m.role == "user" and m.content == "Tell me more."
            for m in kwargs["messages"]
        )
        # And the submit_plan tool spec must be passed.
        tools = kwargs["tools"]
        assert any(t.name == "submit_plan" for t in tools)
