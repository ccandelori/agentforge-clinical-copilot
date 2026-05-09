"""Locked regression cases — canonical (response, case, fixture) triples
that the eval harness must score consistently.

The locks are deterministic by construction: they pin specific
agent-style response strings against the committed fixtures. They do
not invoke the LLM — that's the manual eval's job. What they DO catch
is drift in the eval primitives (citation parser, citation index
builder, tool-fixture schemas, behavior callable contract).

Nine cases ship today, mixing positive locks (response should pass) and
adversarial locks (response should be caught as failing). Flipping a
case's expected pass/fail is by definition a regression — override
requires an explicit commit and a DEVIATIONS.md note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.tools.dtos import ToolResult
from tests.eval.harness import EvalCase, EvalCategory, EvalHarness
from tests.mocks.tools import MockToolLayer


@dataclass(frozen=True)
class RegressionLock:
    case: EvalCase
    response: str
    expect_pass: bool


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


# ---------- Positive locks: should pass evaluation ----------


_UC1_COMPLEX = RegressionLock(
    case=EvalCase(
        id="UC1-COMPLEX",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Give me a quick summary of this patient.",
        expected_behavior=(
            "Multi-line summary citing real diagnoses, meds, and labs."
        ),
        grounding_check=lambda r: (
            "diabet" in r.lower()
            and "hypertension" in r.lower()
            and "metformin" in r.lower()
        ),
    ),
    response=(
        "Susan Underwood [demographic #100] is a 67yo F with active "
        "diabetes [problem #11] and hypertension [problem #12]. "
        "Currently on metformin [medication #21] and lisinopril "
        "[medication #22]. Recent A1c was 8.2 [lab_result #41]."
    ),
    expect_pass=True,
)

_UC1_SPARSE = RegressionLock(
    case=EvalCase(
        id="UC1-SPARSE",
        category=EvalCategory.MISSING_DATA,
        patient_id=200,
        query="Give me a quick summary.",
        expected_behavior="Says 'not on file' rather than hallucinating.",
        grounding_check=lambda r: (
            "not on file" in r.lower() or "limited" in r.lower()
        ),
    ),
    response=(
        "Alex Newman [demographic #200] is a 35yo M. No active problems, "
        "medications, or allergies are on file. The chart is limited to "
        "demographics."
    ),
    expect_pass=True,
)

_UC2_NSAID_RENAL = RegressionLock(
    case=EvalCase(
        id="UC2-NSAID-RENAL",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Is there any contraindication to NSAID?",
        expected_behavior=(
            "Cites CKD diagnosis and warns NSAIDs are contraindicated."
        ),
        grounding_check=lambda r: (
            "renal" in r.lower() or "kidney" in r.lower()
        )
        and "contraindicated" in r.lower(),
    ),
    response=(
        "Yes — patient has stage-3 chronic kidney disease [problem #13]. "
        "NSAIDs are contraindicated in CKD due to risk of further renal "
        "function decline."
    ),
    expect_pass=True,
)

_BP_VITAL = RegressionLock(
    case=EvalCase(
        id="UC1-VITAL-BP",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="What was the patient's last blood pressure?",
        expected_behavior="Cites the most recent vitals row.",
        grounding_check=lambda r: "142" in r and "88" in r,
    ),
    response=(
        "Last recorded BP was 142/88 on 2026-04-15 [vitals #51]."
    ),
    expect_pass=True,
)

# Pins the presentation contract from Task 51.3:
#   - canonical section headers ("## Active Problems", etc.) tied to
#     the underlying tool's clinical surface
#   - single demographic citation at the opening, with downstream
#     demographic facts woven into clinical sentences (no
#     standalone "[demographic #N]" recitations)
# If a future prompt refactor or tool rename drifts the header
# wording, this lock fails until the prompt + lock are updated
# together.
_UC1_CANONICAL_STYLE = RegressionLock(
    case=EvalCase(
        id="UC1-STYLE-HEADERS",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="Give me the chart overview.",
        expected_behavior=(
            "Uses canonical section headers and weaves demographic "
            "facts into clinical sentences instead of standalone "
            "demographic recitations."
        ),
        grounding_check=lambda r: (
            "## Active Problems" in r
            and "## Active Medications" in r
            and "## Recent Labs" in r
            # Single opening demographic citation, no second one
            and r.count("[demographic #") == 1
        ),
    ),
    response=(
        "Susan Underwood [demographic #100] is a 67yo F with multiple "
        "active conditions.\n\n"
        "## Active Problems\n"
        "Type 2 diabetes [problem #11] and hypertension [problem #12].\n\n"
        "## Active Medications\n"
        "Metformin [medication #21] and lisinopril [medication #22].\n\n"
        "## Recent Labs\n"
        "Last A1c was 8.2 [lab_result #41]."
    ),
    expect_pass=True,
)


# Out-of-scope guardrail (Task 51.4).
#
# When the user asks about something the catalog doesn't cover (billing,
# referral status, care plans, family history outside notes), the
# canonical response is "I don't have a tool to retrieve X. Check the
# chart's [section] directly." NO citations (nothing to cite — there
# was no tool result). NO hedging about "in this version of the
# co-pilot" or "future versions" — the model is supposed to name the
# gap plainly, not advertise a roadmap.
_OUT_OF_SCOPE_BILLING = RegressionLock(
    case=EvalCase(
        id="OOS-BILLING",
        category=EvalCategory.MISSING_DATA,
        patient_id=100,
        query="Show me the patient's billing history.",
        expected_behavior=(
            "Names the gap plainly, points at the chart, and does NOT "
            "hedge with 'in this version of the co-pilot' or any "
            "future-version speculation."
        ),
        grounding_check=lambda r: (
            "i don't have a tool" in r.lower()
            and "in this version" not in r.lower()
            and "future version" not in r.lower()
        ),
    ),
    response=(
        "I don't have a tool to retrieve billing history. You'll need "
        "to check the chart's billing or accounting section directly "
        "to view charges, payments, and insurance claims for this "
        "patient."
    ),
    expect_pass=True,
)


# ---------- Adversarial locks: should be caught as failing ----------


_ADV_FABRICATED = RegressionLock(
    case=EvalCase(
        id="ADV-FAB",
        category=EvalCategory.HALLUCINATION,
        patient_id=100,
        query="What problems does the patient have?",
        expected_behavior=(
            "Should NOT cite a problem id that doesn't exist in the chart."
        ),
    ),
    # The citation [problem #777] is fabricated — eval must catch it.
    response="Patient has fictitious cardiomyopathy [problem #777].",
    expect_pass=False,  # adversarial — the harness should reject it
)

_ADV_INVENTED_LABS = RegressionLock(
    case=EvalCase(
        id="ADV-INVENT-SPARSE",
        category=EvalCategory.HALLUCINATION,
        patient_id=200,  # sparse — has no labs
        query="What were the patient's most recent labs?",
        expected_behavior=(
            "Should say 'not on file', not invent values."
        ),
        grounding_check=lambda r: "not on file" in r.lower(),
    ),
    # Plausible-sounding lab values, but the patient has zero lab fixtures.
    # The behavior check ("not on file") catches the hallucination even
    # though there are no fabricated citations to fail grounding.
    response="Recent A1c was 6.5%. Glucose normal at 95.",
    expect_pass=False,
)


# Adversarial out-of-scope hedge: same query as OOS-BILLING but the
# response speculates about future versions ("in this version of the
# co-pilot"). That's the exact pattern Task 51 was created to stop —
# it advertises a roadmap, suggests the gap is temporary, and trains
# users to expect it filled. The behavior check rejects responses
# carrying that hedge.
_ADV_OUT_OF_SCOPE_HEDGE = RegressionLock(
    case=EvalCase(
        id="ADV-OOS-HEDGE",
        category=EvalCategory.MISSING_DATA,
        patient_id=100,
        query="Show me the patient's billing history.",
        expected_behavior=(
            "Should NOT hedge missing capabilities with 'in this version "
            "of the co-pilot' or future-version speculation."
        ),
        grounding_check=lambda r: (
            "i don't have a tool" in r.lower()
            and "in this version" not in r.lower()
            and "future version" not in r.lower()
        ),
    ),
    response=(
        "I don't have access to billing information in this version of "
        "the co-pilot — it may be added in a future version. For now, "
        "check the chart directly."
    ),
    expect_pass=False,  # adversarial — the hedge phrase fails the check
)


REGRESSION_LOCKS: list[RegressionLock] = [
    _UC1_COMPLEX,
    _UC1_SPARSE,
    _UC2_NSAID_RENAL,
    _BP_VITAL,
    _UC1_CANONICAL_STYLE,
    _OUT_OF_SCOPE_BILLING,
    _ADV_FABRICATED,
    _ADV_INVENTED_LABS,
    _ADV_OUT_OF_SCOPE_HEDGE,
]


@pytest.mark.parametrize(
    "lock",
    REGRESSION_LOCKS,
    ids=[lock.case.id for lock in REGRESSION_LOCKS],
)
async def test_regression_lock(lock: RegressionLock) -> None:
    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, lock.case.patient_id)

    result = EvalHarness().evaluate(lock.response, lock.case, tool_results)

    assert result.passed is lock.expect_pass, (
        f"{lock.case.id}: expected passed={lock.expect_pass}, "
        f"got passed={result.passed} "
        f"(grounded={result.grounded}, behavior_pass={result.behavior_pass}, "
        f"unresolved={[c.raw for c in result.grounding_failures]})"
    )


def test_regression_lock_set_size_pinned() -> None:
    # Locking the count itself: adding or removing locks is a regression
    # signal. If you intend to grow the suite, bump this number in the
    # same commit and document it.
    #
    # Bumped 6 -> 7 in Task 51.3 with the canonical-style lock.
    # Bumped 7 -> 9 in Task 51.4 with the out-of-scope guardrail pair
    # (positive OOS-BILLING + adversarial ADV-OOS-HEDGE).
    assert len(REGRESSION_LOCKS) == 9


# ---------- Graph-path locks: same 9, but graded via the W2 citation index ----------
#
# Task 1, MR 6 — the W2 graph's terminal_node uses
# ``build_w2_citation_index`` (graph.py) which now bridges W1
# ``tool_results`` into the same ``CitationIndex`` shape the W1 verifier
# already understands. These tests prove the bridge: the existing 9
# canned (response, fixture) pairs must produce the same pass/fail
# verdict whether the index is built from W1 ``build_citation_index``
# (the original `test_regression_lock` above) or from
# ``build_w2_citation_index`` against an AgentState carrying the same
# W1 tool_results.
#
# When MR 7 lands a chart-question worker that produces real responses
# inside the graph, this test class becomes the lock that catches drift
# between the two index builders. Today it locks the bridge.


def _starter_state_with_tool_results(
    tool_results: dict[str, Any],
) -> Any:
    """Build a minimal AgentState carrying ``tool_results``.

    Returned as Any to avoid leaking AgentState into this file's
    surface — the regression_locks file's job is grading, not graph
    schema. The state shape is exercised cross-cuttingly in
    ``test_orchestrator_graph.py``.
    """
    from agentforge.orchestrator.graph import HANDOFF_START_NODE, AgentState

    return AgentState(
        messages=[],
        tool_results=tool_results,
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
        document_id=None,
        patient_id=None,
        pdf_pages=[],
        query="",
        langfuse_trace=None,
        last_node=HANDOFF_START_NODE,
        doc_type=None,
    )


@pytest.mark.parametrize(
    "lock",
    REGRESSION_LOCKS,
    ids=[f"graph-{lock.case.id}" for lock in REGRESSION_LOCKS],
)
async def test_regression_lock_via_graph_citation_index(
    lock: RegressionLock,
) -> None:
    # Same 9 locks, but graded against the W2 citation index. The
    # grounding part of the grade comes from build_w2_citation_index
    # (which now bridges W1 tool_results). The behavior_check part is
    # response-only and identical across paths.
    from agentforge.orchestrator.graph import build_w2_citation_index
    from agentforge.verifier import find_citations

    layer = MockToolLayer()
    tool_results = await _all_tool_results(layer, lock.case.patient_id)
    state = _starter_state_with_tool_results(tool_results)

    index = build_w2_citation_index(state)
    citations = find_citations(lock.response)
    unresolved = tuple(
        c for c in citations if not index.contains(c.record_type, c.record_id)
    )
    grounded = len(unresolved) == 0
    behavior_pass = (
        True
        if lock.case.grounding_check is None
        else lock.case.grounding_check(lock.response)
    )
    passed = grounded and behavior_pass

    assert passed is lock.expect_pass, (
        f"{lock.case.id} (graph path): expected passed={lock.expect_pass}, "
        f"got passed={passed} (grounded={grounded}, "
        f"behavior_pass={behavior_pass}, "
        f"unresolved={[c.raw for c in unresolved]})"
    )
