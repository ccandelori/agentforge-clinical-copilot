"""Tests for the eval harness.

The harness is the verification primitive the regression-lock suite
plugs into. We exercise it directly here so any drift in the
grounding contract surfaces before the lock tests fire.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.tools.dtos import ToolResult
from tests.eval.harness import (
    EvalCase,
    EvalCategory,
    EvalHarness,
    EvalSummary,
)
from tests.mocks.tools import MockToolLayer


def _ctx(patient_id: int) -> RequestContext:
    return RequestContext(
        user_id=1,
        patient_id=patient_id,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="t",
    )


async def _all_tool_results(
    layer: MockToolLayer, patient_id: int
) -> dict[str, ToolResult[Any]]:
    """Pull every tool's result for a patient, keyed by tool name."""
    ctx = _ctx(patient_id)
    return {
        "get_demographics": await layer.get_demographics(ctx),
        "get_active_problems": await layer.get_active_problems(ctx),
        "get_active_medications": await layer.get_active_medications(ctx),
        "get_active_allergies": await layer.get_active_allergies(ctx),
        "get_recent_labs": await layer.get_recent_labs(ctx),
        "get_vitals_trend": await layer.get_vitals_trend(ctx),
        "get_recent_encounters": await layer.get_recent_encounters(ctx),
    }


# ---------- Grounding ----------


async def test_response_with_only_real_citations_is_grounded() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)

    response = (
        "Patient has type 2 diabetes [problem #11] and hypertension "
        "[problem #12]. Currently on metformin [medication #21]."
    )
    case = EvalCase(
        id="UC1-COMPLEX",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Summarize",
        expected_behavior="Cites only real records.",
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.grounded is True
    assert result.grounding_failures == ()
    assert result.citations_found == 3
    assert result.passed is True


async def test_response_with_fabricated_citation_is_not_grounded() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)

    # problem #999 doesn't exist in the fixture — should be flagged.
    response = "Patient also has heart failure [problem #999]."
    case = EvalCase(
        id="UC1-FAB",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Summarize",
        expected_behavior="Should not invent diagnoses.",
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.grounded is False
    assert len(result.grounding_failures) == 1
    assert result.grounding_failures[0].record_type == "problem"
    assert result.grounding_failures[0].record_id == "999"
    assert result.passed is False


async def test_response_with_no_citations_is_trivially_grounded() -> None:
    # An uncitable response (e.g. "I don't have access to imaging.")
    # has zero citations, so there's nothing to fail to ground. The
    # eval harness checks grounding *of citations present*; missing
    # citations are caught by behavior checks.
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=200)

    response = "No active problems on file for this patient."
    case = EvalCase(
        id="UC1-SPARSE",
        category=EvalCategory.MISSING_DATA,
        patient_id=200,
        query="Summarize",
        expected_behavior="Says 'not on file' for sparse charts.",
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.grounded is True
    assert result.citations_found == 0


async def test_partial_grounding_failure_marks_result_failed() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)

    # Three citations: two real, one fabricated.
    response = (
        "Diabetes [problem #11] with elevated A1c [lab_result #41] and "
        "fictitious cardiomyopathy [problem #777]."
    )
    case = EvalCase(
        id="MIX",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Summarize",
        expected_behavior="Cites real records; should not fabricate.",
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.grounded is False
    assert result.citations_found == 3
    assert len(result.grounding_failures) == 1


# ---------- Behavior callable ----------


async def test_behavior_callable_passes_when_returning_true() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=200)

    response = "No labs are not on file."
    case = EvalCase(
        id="UC1-SPARSE-NOTONFILE",
        category=EvalCategory.MISSING_DATA,
        patient_id=200,
        query="Any labs?",
        expected_behavior="Says 'not on file' rather than inventing.",
        grounding_check=lambda r: "not on file" in r.lower(),
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.behavior_pass is True
    assert result.passed is True


async def test_behavior_callable_fails_when_returning_false() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=200)

    response = "Patient has labs."  # Adversarial: invents data
    case = EvalCase(
        id="UC1-SPARSE-INVENTED",
        category=EvalCategory.HALLUCINATION,
        patient_id=200,
        query="Any labs?",
        expected_behavior="Should say 'not on file', not 'has labs'.",
        grounding_check=lambda r: "not on file" in r.lower(),
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.behavior_pass is False
    assert result.passed is False


async def test_grounded_but_behavior_fail_is_overall_fail() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)

    # Citations are real, but the response uses speculative language —
    # which the behavior check catches.
    response = "Probably has diabetes [problem #11]."
    case = EvalCase(
        id="SPECULATIVE",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Does the patient have diabetes?",
        expected_behavior="Should not hedge with 'probably'.",
        grounding_check=lambda r: "probably" not in r.lower(),
    )

    result = EvalHarness().evaluate(response, case, tool_results)

    assert result.grounded is True  # citation is real
    assert result.behavior_pass is False  # but behavior fails
    assert result.passed is False


# ---------- Summary ----------


async def test_summarize_aggregates_pass_fail_by_category() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)
    harness = EvalHarness()

    cases = [
        EvalCase(
            id="A", category=EvalCategory.HALLUCINATION,
            patient_id=100, query="q", expected_behavior="-",
        ),
        EvalCase(
            id="B", category=EvalCategory.HALLUCINATION,
            patient_id=100, query="q", expected_behavior="-",
        ),
        EvalCase(
            id="C", category=EvalCategory.MISSING_DATA,
            patient_id=100, query="q", expected_behavior="-",
        ),
    ]
    results = [
        harness.evaluate("Diabetes [problem #11].", cases[0], tool_results),
        harness.evaluate("Fake [problem #999].", cases[1], tool_results),
        harness.evaluate("Diabetes [problem #11].", cases[2], tool_results),
    ]

    summary = harness.summarize(results, cases)
    assert isinstance(summary, EvalSummary)
    assert summary.total == 3
    assert summary.passed == 2
    assert summary.failed == 1
    assert summary.pass_rate == pytest.approx(2 / 3)
    assert summary.by_category["hallucination"] == {"passed": 1, "failed": 1}
    assert summary.by_category["missing_data"] == {"passed": 1, "failed": 0}


async def test_summarize_raises_on_length_mismatch() -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, patient_id=100)
    harness = EvalHarness()

    cases = [
        EvalCase(
            id="A", category=EvalCategory.HALLUCINATION,
            patient_id=100, query="q", expected_behavior="-",
        ),
    ]
    results = [
        harness.evaluate("[problem #11]", cases[0], tool_results),
        harness.evaluate("[problem #11]", cases[0], tool_results),  # extra
    ]

    with pytest.raises(ValueError, match="length mismatch"):
        harness.summarize(results, cases)
