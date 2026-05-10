"""Self-test that proves the gate blocks a real regression (Task 19).

The gate's per-category thresholds + regression-drop arithmetic are
unit-tested in :mod:`tests.eval.gate.test_gate`. Those tests synthesise
pass-rate dicts directly. They prove the *math* — they do not prove the
*end-to-end pipeline* (runner → scoring → gate) catches a regression
that flows through the harness layers.

This module fills that gap. It runs the full W2 suite (loading the 50
golden YAML cases, dispatching through a callable adapter, grading via
the harness, aggregating per-category, and feeding the result to the
gate) under a *regressed* adapter that injects fabricated output for a
small subset of cases. The contract: the gate must report a failing
verdict.

Sibling test
------------
:mod:`tests.eval.gate.test_gate_blocks_regression_replay` is the W2
HARD-GATE counterpart that runs against the **real synthesizer code
path** via recorded LLM fixtures. This file exercises the runner +
harness + gate layers against a hand-crafted SupervisorOutput; the
replay sibling proves a code-level regression (e.g. someone edits
the synthesizer to drop citations) ALSO trips the gate. CI runs both
on every push.

Why citation-strip stands in for "fabricated lab value"
-------------------------------------------------------
The Task 19 brief frames the regression as "fabricated ``LabValue``
(e.g. A1c=15.5% when the case expects 8.2%)". The W2 harness wiring
(see :mod:`tests.eval.harness_w2._JUDGE_BY_CATEGORY`) only routes
``EvalCategory.HALLUCINATION`` and ``EvalCategory.REFUSAL`` cases to the
LLM judge. The W2 yaml suite categorises cases as ``extraction``,
``evidence_retrieval``, ``citations``, ``refusal``, ``missing_data`` —
so most cases run programmatic-only and never see a "factually
consistent" judge call. A pure value-fabrication can therefore slip
past the harness even though it would slip past nothing in production.

The closest programmatic analogue is **a response that asserts a
clinical value with no citation backing it** — the W2 contract is
"every claim carries a Citation", and the programmatic
``check_citation_present`` enforces that. The regressed adapter here
emits exactly that: a response stating ``A1c = 15.5%`` (the fabricated
value) with the citation deliberately stripped. The regression lands
on the ``citations`` category pass rate, which the gate's
``citation_present`` threshold + regression check both catch.

Manual trigger
--------------
The full suite runs in CI behind the ``gate_validation`` marker
(deselected by default — see ``pyproject.toml``). To run it locally::

    cd sidecar
    uv run pytest -m gate_validation -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.llm.types import LLMResponse
from agentforge.schemas.citation import (
    Citation,
    PageBBox,
    SourceType,
)
from tests.eval.gate.config import EvalGateConfig
from tests.eval.gate.gate import (
    GateVerdict,
    ViolationKind,
    evaluate_gate,
    run_gate_cli,
)
from tests.eval.gate.runner_w2 import (
    SupervisorOutput,
    load_week2_cases,
    run_week2_suite,
)
from tests.eval.gate.scoring import summarize_by_category
from tests.eval.graders.llm_judge_w2 import LLMJudge
from tests.eval.harness import EvalCase, EvalCategory
from tests.eval.harness_w2 import EvalHarnessW2

# Regressed cases inject this fabricated A1c value. The number itself
# is not load-bearing — what matters is that the *response asserts a
# clinical claim with no citation*. The string is here so a future
# reader of a failing gate report can grep for it and confirm the
# regression is the deliberately-injected one and not a real bug.
_FABRICATED_A1C_RESPONSE: str = "A1c = 15.5% (no source attached)."


def _intake_citation() -> Citation:
    """A clean structured citation — what a passing case attaches."""
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9),
    )


def _good_payload() -> dict[str, Any]:
    """A schema-valid structured citation payload — passes ``check_schema_valid``."""
    return {
        "source_type": "openemr_record",
        "source_id": "5",
        "page_or_section": "problem #5",
        "field_or_chunk_id": "title",
        "quote_or_value": "Hypertension",
    }


def _passing_output(_case: EvalCase) -> SupervisorOutput:
    """Clean output every grading layer accepts as a pass."""
    return SupervisorOutput(
        response="The chief complaint is chest pain [problem #5].",
        sources="patient record: chief complaint chest pain on exertion",
        structured_citation_payload=_good_payload(),
        structured_citations=(_intake_citation(),),
        logs=("clean trace line",),
    )


def _regressed_output(_case: EvalCase) -> SupervisorOutput:
    """Fabricated clinical claim with no citation backing it.

    The response asserts ``A1c = 15.5%`` — a clinical value with no
    inline ``[record_type #id]`` token and no structured ``Citation``
    attached. The W2 ``check_citation_present`` programmatic check
    fails, so the case's :class:`W2EvalResult.passed` is False.
    """
    return SupervisorOutput(
        response=_FABRICATED_A1C_RESPONSE,
        sources="",
        structured_citation_payload=_good_payload(),
        structured_citations=(),
        logs=(),
    )


def _judge_response(verdict: str) -> LLMResponse:
    return LLMResponse(
        text=f"VERDICT: {verdict}\nRATIONALE: stub",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=120,
        output_tokens=15,
    )


def _build_harness(*, judge_verdict: str = "PASS") -> EvalHarnessW2:
    """Build a harness with a mocked LLM judge — no real LLM calls."""
    llm = AsyncMock()
    llm.complete.return_value = _judge_response(judge_verdict)
    langfuse = MagicMock()
    judge = LLMJudge(llm=llm, langfuse=langfuse, model="claude-sonnet-4-6")
    trace = MagicMock()
    trace.trace_id = "test-trace"
    return EvalHarnessW2(judge=judge, trace=trace)


def _make_regressed_supervisor(
    cases: list[EvalCase],
    *,
    regress_count: int = 4,
    target_category: EvalCategory = EvalCategory.CITATIONS,
) -> tuple[Any, set[str]]:
    """Build a supervisor adapter that fabricates output for a subset.

    Picks the first ``regress_count`` cases whose category is
    ``target_category`` and routes them through ``_regressed_output``;
    every other case gets ``_passing_output``. Returns the supervisor
    callable plus the set of regressed case ids (for assertions).
    """
    target_value = target_category.value
    regressed_ids: set[str] = set()
    for case in cases:
        if case.category.value == target_value and len(regressed_ids) < regress_count:
            regressed_ids.add(case.id)

    def supervisor(case: EvalCase) -> SupervisorOutput:
        if case.id in regressed_ids:
            return _regressed_output(case)
        return _passing_output(case)

    return supervisor, regressed_ids


def _baseline_all_one() -> dict[str, float]:
    return {
        "extraction": 1.0,
        "evidence_retrieval": 1.0,
        "citations": 1.0,
        "refusal": 1.0,
        "missing_data": 1.0,
    }


def _strict_config() -> EvalGateConfig:
    return EvalGateConfig(
        category_thresholds={
            "schema_valid": 0.9,
            "citation_present": 0.9,
            "factually_consistent": 0.9,
            "safe_refusal": 0.9,
            "no_phi_in_logs": 0.9,
        },
        regression_threshold=0.05,
        llm_judge_model="claude-sonnet-4-6",
        llm_judge_temperature=0,
    )


@pytest.mark.gate_validation
class TestGateBlocksRegression:
    """End-to-end self-test: gate must fail when the suite regresses."""

    async def test_clean_supervisor_passes_gate(self) -> None:
        """Sanity check: the *passing* adapter clears the gate.

        Without this, a green ``test_regressed_supervisor_fails_gate``
        could mean "the gate always fails" rather than "the gate fires
        when there is a real regression". This pins the negative space.
        """
        cases = load_week2_cases()
        assert len(cases) == 50
        harness = _build_harness(judge_verdict="PASS")

        results = await run_week2_suite(cases=cases, supervisor=_passing_output, harness=harness)
        rates = summarize_by_category(results)
        verdict = evaluate_gate(
            current=rates,
            baseline=_baseline_all_one(),
            config=_strict_config(),
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.passed, (
            f"clean adapter must pass the gate; violations={verdict.violations} rates={rates}"
        )

    async def test_regressed_supervisor_fails_gate(self) -> None:
        """Inject a small fabrication; the gate must block it.

        Regresses 4 of the 10 ``citations`` cases by emitting a
        clinical claim (``A1c = 15.5%``) with no citation. The
        ``citations`` category pass-rate drops from 1.0 → 0.6 — well
        outside the gate's 0.05 regression band and below the 0.9
        absolute floor. The gate must surface at least one violation
        and the run-cli surface must exit non-zero.
        """
        cases = load_week2_cases()
        assert len(cases) == 50
        harness = _build_harness(judge_verdict="PASS")

        supervisor, regressed_ids = _make_regressed_supervisor(
            cases, regress_count=4, target_category=EvalCategory.CITATIONS
        )
        assert len(regressed_ids) == 4, (
            f"fixture sanity: expected 4 citations cases to regress; got {regressed_ids}"
        )

        results = await run_week2_suite(cases=cases, supervisor=supervisor, harness=harness)
        rates = summarize_by_category(results)

        # The pass-rate drop on the regressed category must exceed the
        # gate's 5 % regression threshold — that is the headline claim
        # of this self-test.
        baseline = _baseline_all_one()
        citations_drop = baseline["citations"] - rates["citations"]
        assert citations_drop > 0.05, (
            f"expected citations pass-rate to drop > 5% under regression; "
            f"got drop={citations_drop:.3f} (baseline={baseline['citations']}, "
            f"current={rates['citations']})"
        )

        # Other categories should still be at 1.0 — the regression is
        # localised to citations cases, so the rest of the suite must
        # remain clean. This guards against the regressed adapter
        # leaking failure into unrelated categories.
        for category in ("extraction", "evidence_retrieval", "refusal", "missing_data"):
            assert rates[category] == pytest.approx(1.0), (
                f"non-regressed category {category} unexpectedly dropped: rate={rates[category]}"
            )

        # Gate must report a failing verdict.
        verdict = evaluate_gate(
            current=rates,
            baseline=baseline,
            config=_strict_config(),
        )
        assert verdict.passed is False, (
            f"gate should block this regression; rates={rates} violations={verdict.violations}"
        )

        # The violation set must include at least one citations-scoped
        # entry. Both REGRESSION (drop > 5%) and BELOW_THRESHOLD
        # (current < 0.9) are expected to fire here; either is enough
        # to prove the gate caught the regression.
        citations_violations = [v for v in verdict.violations if v.category == "citations"]
        assert citations_violations, (
            f"expected at least one citations violation; got {verdict.violations}"
        )
        kinds = {v.kind for v in citations_violations}
        assert ViolationKind.REGRESSION in kinds or ViolationKind.BELOW_THRESHOLD in kinds, (
            f"expected REGRESSION or BELOW_THRESHOLD on citations; got {kinds}"
        )

    async def test_regressed_run_cli_exits_non_zero(self, tmp_path: Path) -> None:
        """The CLI surface (``run_gate_cli``) must exit non-zero too.

        Mirrors :func:`test_regressed_supervisor_fails_gate` but goes
        through the JSON-results-file CLI path that CI consumes (Task
        20 wires this into GitLab). Confirms the regression surfaces
        all the way through to the process exit code.
        """
        cases = load_week2_cases()
        harness = _build_harness(judge_verdict="PASS")
        supervisor, _ = _make_regressed_supervisor(
            cases, regress_count=4, target_category=EvalCategory.CITATIONS
        )
        results = await run_week2_suite(cases=cases, supervisor=supervisor, harness=harness)
        rates = summarize_by_category(results)

        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(_baseline_all_one()), encoding="utf-8")
        results_path = tmp_path / "results.json"

        exit_code = run_gate_cli(
            current_rates=rates,
            baseline_path=baseline_path,
            results_path=results_path,
            config=_strict_config(),
        )
        assert exit_code != 0, f"run_gate_cli must exit non-zero on a regressed run; rates={rates}"
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert any(v["category"] == "citations" for v in payload["violations"])
