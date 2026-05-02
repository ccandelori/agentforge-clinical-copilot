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
    Plan,
    PlannedToolCall,
    UseCase,
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
