"""Unit tests for EvalRunner.

Exercises EvalRunner with a mocked Orchestrator so no real LLM or
sidecar process is required. All tests are pure-unit and run in CI.

No @pytest.mark.eval — that marker is reserved for the live-sidecar
baseline suite in tests/eval/baseline/.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.tools.dtos import ToolResult, ToolResultMetadata
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult

from tests.eval.harness import EvalCase, EvalCategory
from tests.eval.runner import EvalRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metadata(tool_name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=tool_name,
        fetched_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        data_freshness_seconds=60,
        source="test",
    )


def _problems_result_with_id(problem_id: int) -> ProblemsResult:
    """Build a ProblemsResult containing one ProblemItem with the given id."""
    payload = ProblemsPayload(
        problems=(
            ProblemItem(id=problem_id, title="Hypertension"),
        )
    )
    return ProblemsResult(
        metadata=_metadata("get_active_problems"),
        payload=payload,
    )


def _orchestrator_returning(text: str) -> AsyncMock:
    """Return an AsyncMock whose .turn() coroutine returns ``text``."""
    mock = AsyncMock()
    mock.turn.return_value = text
    return mock


def _grounded_case(patient_id: int = 7) -> EvalCase:
    """An EvalCase whose grounding check passes when [problem #42] is cited."""
    return EvalCase(
        id="test-grounded",
        category=EvalCategory.HALLUCINATION,
        patient_id=patient_id,
        query="What are the active problems?",
        expected_behavior="cites real problem record",
    )


def _ungrounded_case(patient_id: int = 7) -> EvalCase:
    """An EvalCase intended to produce an ungrounded citation."""
    return EvalCase(
        id="test-ungrounded",
        category=EvalCategory.HALLUCINATION,
        patient_id=patient_id,
        query="What are the active problems?",
        expected_behavior="no citation required",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunCasePasses:
    @pytest.mark.asyncio
    async def test_run_case_passes_when_response_is_grounded(self) -> None:
        """result.passed is True when the response cites a real record ID."""
        # Tool results include problem #42.
        tool_results: dict[str, ToolResult[Any]] = {
            "get_active_problems": _problems_result_with_id(42),
        }
        # The model's response cites [problem #42] — resolves in the index.
        orchestrator = _orchestrator_returning(
            "The patient has hypertension [problem #42]."
        )
        runner = EvalRunner(orchestrator)

        result = await runner.run_case(_grounded_case(), tool_results)

        assert result.passed is True
        assert result.grounded is True
        assert result.behavior_pass is True


class TestRunCaseFails:
    @pytest.mark.asyncio
    async def test_run_case_fails_when_response_is_ungrounded(self) -> None:
        """result.grounded is False when a citation doesn't resolve."""
        # Tool results contain problem #42, but the response cites #999.
        tool_results: dict[str, ToolResult[Any]] = {
            "get_active_problems": _problems_result_with_id(42),
        }
        orchestrator = _orchestrator_returning(
            "The patient has diabetes [problem #999]."
        )
        runner = EvalRunner(orchestrator)

        result = await runner.run_case(_ungrounded_case(), tool_results)

        assert result.grounded is False
        assert result.passed is False
        # The unresolved citation should be in grounding_failures.
        assert len(result.grounding_failures) == 1
        assert result.grounding_failures[0].record_id == "999"


class TestRunSuiteSummary:
    @pytest.mark.asyncio
    async def test_run_suite_returns_summary_with_correct_counts(self) -> None:
        """Summary has total=2, passed=1, failed=1 for 1 pass + 1 fail."""
        tool_results_with_42: dict[str, ToolResult[Any]] = {
            "get_active_problems": _problems_result_with_id(42),
        }
        tool_results_with_42_too: dict[str, ToolResult[Any]] = {
            "get_active_problems": _problems_result_with_id(42),
        }

        # First case: passes — cites real record.
        passing_case = _grounded_case(patient_id=7)
        # Second case: fails — cites non-existent record.
        failing_case = _ungrounded_case(patient_id=8)

        # Mock returns grounded text for first call, ungrounded for second.
        orchestrator = AsyncMock()
        orchestrator.turn.side_effect = [
            "Active problems: hypertension [problem #42].",
            "Active problems: diabetes [problem #999].",
        ]

        runner = EvalRunner(orchestrator)
        suite_cases = [
            (passing_case, tool_results_with_42),
            (failing_case, tool_results_with_42_too),
        ]

        summary = await runner.run_suite(suite_cases)

        assert summary.total == 2
        assert summary.passed == 1
        assert summary.failed == 1


class TestRunCaseRequestContext:
    @pytest.mark.asyncio
    async def test_run_case_builds_request_context_with_correct_patient_id(
        self,
    ) -> None:
        """The RequestContext passed to Orchestrator.turn has patient_id=case.patient_id."""
        case = EvalCase(
            id="ctx-check",
            category=EvalCategory.AUTH_BOUNDARY,
            patient_id=55,
            query="Summarize the chart.",
            expected_behavior="responds with chart data",
        )
        orchestrator = _orchestrator_returning("chart summary")
        runner = EvalRunner(orchestrator)

        await runner.run_case(case, {})

        # Inspect the RequestContext positional arg passed to turn().
        assert orchestrator.turn.await_count == 1
        ctx_arg: RequestContext = orchestrator.turn.call_args.args[0]
        assert isinstance(ctx_arg, RequestContext)
        assert ctx_arg.patient_id == 55
        # Fixed harness credentials.
        assert ctx_arg.user_id == 1
        assert ctx_arg.username == "eval-harness"
        assert ctx_arg.role == "clinician"
        assert ctx_arg.breakglass_flag is False
        assert ctx_arg.raw_token == "eval-token"


class TestRunCaseBehaviorCheck:
    @pytest.mark.asyncio
    async def test_run_case_passes_behavior_check_when_present(self) -> None:
        """behavior_pass=True when grounding_check lambda succeeds."""
        # The case requires the response to contain the word "hypertension".
        case = EvalCase(
            id="behavior-check",
            category=EvalCategory.HALLUCINATION,
            patient_id=7,
            query="List all problems.",
            expected_behavior="mentions hypertension",
            grounding_check=lambda resp: "hypertension" in resp.lower(),
        )
        tool_results: dict[str, ToolResult[Any]] = {
            "get_active_problems": _problems_result_with_id(42),
        }
        # Response contains the keyword AND cites a real record.
        orchestrator = _orchestrator_returning(
            "The patient has Hypertension [problem #42]."
        )
        runner = EvalRunner(orchestrator)

        result = await runner.run_case(case, tool_results)

        assert result.behavior_pass is True
        assert result.passed is True
