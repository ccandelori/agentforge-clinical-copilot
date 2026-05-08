"""Pre-commit eval-smoke suite (Task 23).

Runs the 10-case representative subset of the 50 W2 eval cases (selected
via ``tags: [eval_smoke]`` in ``tests/eval/cases/week2/*.yaml``) through
:func:`run_week2_suite` with a **mocked supervisor + mocked LLM judge**.
No real Anthropic calls, no live sidecar — the pre-commit hook invokes
this suite via ``uv run pytest -m eval_smoke -q`` and the whole run must
complete in <30s.

Architecture note: the production W2 eval gate (Task 18) layers a
deterministic :class:`ProgrammaticChecks` pass under an
:class:`LLMJudge` (binary PASS/FAIL on factually_consistent /
safe_refusal). The smoke test exercises both layers with mocks so a
refactor that breaks either contract trips the hook before the commit
lands.

This module is deselected from the default pytest run (the
``eval_smoke`` marker is excluded by ``addopts`` in ``pyproject.toml``).
Invoke explicitly with ``-m eval_smoke``.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.llm.types import LLMResponse
from agentforge.schemas.citation import Citation, PageBBox, SourceType

from tests.eval.gate.runner_w2 import (
    SupervisorOutput,
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase
from tests.eval.harness_w2 import EvalHarnessW2

# Wall-clock budget for the entire 10-case suite under the pre-commit hook.
# Empirically the suite completes in well under a second with mocks; the
# cushion is for cold-cache CI runs where pytest collection dominates.
_SMOKE_BUDGET_SECONDS = 30.0


def _smoke_cases() -> list[EvalCase]:
    """Return the eval_smoke-tagged subset of the 50 W2 cases."""
    smoke = [c for c in load_week2_cases() if "eval_smoke" in c.tags]
    if len(smoke) != 10:
        raise AssertionError(
            f"eval_smoke selector resolved to {len(smoke)} cases, expected 10. "
            f"IDs: {[c.id for c in smoke]}"
        )
    return smoke


def _good_payload() -> dict[str, Any]:
    """A structured-citation payload the programmatic schema check accepts."""
    return {
        "source_type": "openemr_record",
        "source_id": "5",
        "page_or_section": "problem #5",
        "field_or_chunk_id": "title",
        "quote_or_value": "Hypertension",
    }


def _intake_citation() -> Citation:
    """A high-confidence INTAKE_FORM citation (passes the bbox floor)."""
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )


def _passing_supervisor_output(_case: EvalCase) -> SupervisorOutput:
    """Mock supervisor output the W2 harness's programmatic layer accepts."""
    return SupervisorOutput(
        response="The chief complaint is chest pain [problem #5].",
        sources="patient record: hypertension",
        structured_citation_payload=_good_payload(),
        structured_citations=(_intake_citation(),),
        logs=("clean trace line",),
    )


def _mock_judge_response(verdict: str = "PASS") -> LLMResponse:
    return LLMResponse(
        text=f"VERDICT: {verdict}\nRATIONALE: smoke fixture",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=15,
    )


def _build_mocked_harness() -> EvalHarnessW2:
    """W2 harness wired with a deterministic mock LLM judge.

    The mock LLM returns ``VERDICT: PASS`` on every call, so any case
    whose category routes through the judge (``hallucination`` /
    ``refusal``) gets a clean PASS without hitting Anthropic.
    """
    mock_llm = AsyncMock()
    mock_llm.complete.return_value = _mock_judge_response("PASS")
    mock_langfuse = MagicMock()
    judge = LLMJudge(
        llm=mock_llm, langfuse=mock_langfuse, model="claude-sonnet-4-6"
    )
    mock_trace = MagicMock()
    mock_trace.trace_id = "smoke-trace"
    return EvalHarnessW2(judge=judge, trace=mock_trace)


# ---------------------------------------------------------------------------
# The hook entrypoints — one parametrized per-case test (so a failure
# surfaces with the case id) plus one suite-wide budget guard.
# ---------------------------------------------------------------------------


@pytest.mark.eval_smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("case", _smoke_cases(), ids=lambda c: c.id)
async def test_smoke_case_passes_with_mocked_supervisor_and_judge(
    case: EvalCase,
) -> None:
    """Each tagged case grades PASS under the mocked W2 pipeline.

    Uses the same ``run_week2_suite`` entrypoint the production W2
    gate uses (Task 18) so the smoke hook regresses on the real
    code path, not a parallel one.
    """
    harness = _build_mocked_harness()
    results = await run_week2_suite(
        cases=[case],
        supervisor=_passing_supervisor_output,
        harness=harness,
    )
    assert len(results) == 1
    eval_result = results[0].eval_result
    assert eval_result.passed, (
        f"Smoke case {case.id} ({case.category.value}) failed: "
        f"programmatic_passed={eval_result.programmatic.passed}, "
        f"judge_outcome={eval_result.judge_outcome}"
    )


@pytest.mark.eval_smoke
@pytest.mark.asyncio
async def test_smoke_suite_completes_under_30s() -> None:
    """The full 10-case suite must finish well under the hook's 30s budget.

    Fail-fast guard: if the smoke suite ever drifts toward the budget
    (someone wires a real LLM call by accident, or the W2 graders pull
    in a network dependency), the hook starts blocking commits noticeably
    and developers will paper over it. Better to fail loudly here.
    """
    cases = _smoke_cases()
    harness = _build_mocked_harness()

    start = time.perf_counter()
    results = await run_week2_suite(
        cases=cases,
        supervisor=_passing_supervisor_output,
        harness=harness,
    )
    elapsed = time.perf_counter() - start

    assert len(results) == 10
    assert all(r.eval_result.passed for r in results), (
        "Smoke suite had failures: "
        f"{[r.case.id for r in results if not r.eval_result.passed]}"
    )
    assert elapsed < _SMOKE_BUDGET_SECONDS, (
        f"Smoke suite took {elapsed:.2f}s, exceeds {_SMOKE_BUDGET_SECONDS}s "
        f"budget. Mocks may have regressed to a real network call."
    )
