"""Tests for the eval-gate verdict logic (Task 18.4).

The gate consumes a per-category pass-rate dict (from 18.3) plus a
baseline JSON (this subtask ships ``baselines/week2.json``) plus the
config (18.1) and emits a structured verdict:

  * ``passed`` — boolean overall verdict
  * ``violations`` — list of per-category reasons the gate failed

Two failure modes:
  1. Absolute floor — any category below its configured threshold.
  2. Regression — any category dropping more than ``regression_threshold``
     from the baseline.

Both checks run on every category, so a single run can surface multiple
problems at once. The CLI (``run_gate_cli``) writes a JSON results file
and exits non-zero on any violation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.gate.config import EvalGateConfig
from tests.eval.gate.gate import (
    BASELINE_PATH,
    GateVerdict,
    Violation,
    ViolationKind,
    evaluate_gate,
    load_baseline,
    run_gate_cli,
)


def _config(
    *, regression: float = 0.05, threshold: float = 0.9
) -> EvalGateConfig:
    return EvalGateConfig(
        category_thresholds={
            "schema_valid": threshold,
            "citation_present": threshold,
            "factually_consistent": threshold,
            "safe_refusal": threshold,
            "no_phi_in_logs": threshold,
        },
        regression_threshold=regression,
        llm_judge_model="claude-sonnet-4-6",
        llm_judge_temperature=0,
    )


def _baseline_all_one() -> dict[str, float]:
    return {
        "extraction": 1.0,
        "evidence_retrieval": 1.0,
        "citations": 1.0,
        "refusal": 1.0,
        "missing_data": 1.0,
    }


# ---------------------------------------------------------------------------
# Pass paths
# ---------------------------------------------------------------------------


class TestEvaluateGatePass:
    def test_all_above_threshold_with_no_baseline_drop_passes(self) -> None:
        # All categories within 5 % of baseline AND above the 0.9 floor.
        verdict = evaluate_gate(
            current={
                "extraction": 1.0,
                "evidence_retrieval": 0.97,
                "citations": 0.95,
                "refusal": 1.0,
                "missing_data": 0.97,
            },
            baseline=_baseline_all_one(),
            config=_config(),
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.passed is True
        assert verdict.violations == ()

    def test_drop_within_regression_tolerance_passes(self) -> None:
        # Baseline 1.0, current 0.96 → drop is 0.04, under the 5% gate.
        verdict = evaluate_gate(
            current={
                "extraction": 0.96,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline=_baseline_all_one(),
            config=_config(),
        )
        assert verdict.passed is True


# ---------------------------------------------------------------------------
# Absolute-floor violations
# ---------------------------------------------------------------------------


class TestEvaluateGateAbsoluteFloor:
    def test_category_below_threshold_fails(self) -> None:
        verdict = evaluate_gate(
            current={
                "extraction": 0.85,  # below 0.9 threshold
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline=_baseline_all_one(),
            config=_config(),
        )
        assert verdict.passed is False
        kinds = {v.kind for v in verdict.violations}
        assert ViolationKind.BELOW_THRESHOLD in kinds
        # The offending category is named in the violation
        ext_violation = next(
            v for v in verdict.violations
            if v.category == "extraction"
            and v.kind is ViolationKind.BELOW_THRESHOLD
        )
        assert ext_violation.current == pytest.approx(0.85)
        assert ext_violation.threshold == pytest.approx(0.9)

    def test_multiple_categories_below_threshold_yields_multiple_violations(
        self,
    ) -> None:
        verdict = evaluate_gate(
            current={
                "extraction": 0.5,
                "evidence_retrieval": 0.6,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline=_baseline_all_one(),
            config=_config(),
        )
        assert verdict.passed is False
        below = [
            v for v in verdict.violations
            if v.kind is ViolationKind.BELOW_THRESHOLD
        ]
        assert {v.category for v in below} == {
            "extraction",
            "evidence_retrieval",
        }


# ---------------------------------------------------------------------------
# Regression violations
# ---------------------------------------------------------------------------


class TestEvaluateGateRegression:
    def test_drop_exceeding_regression_threshold_fails(self) -> None:
        # Baseline 1.0 → current 0.94 = 0.06 drop > 0.05 regression gate.
        verdict = evaluate_gate(
            current={
                "extraction": 0.94,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline=_baseline_all_one(),
            config=_config(),
        )
        assert verdict.passed is False
        regressions = [
            v for v in verdict.violations
            if v.kind is ViolationKind.REGRESSION
        ]
        assert len(regressions) == 1
        assert regressions[0].category == "extraction"
        assert regressions[0].drop == pytest.approx(0.06)

    def test_improvement_over_baseline_does_not_fail(self) -> None:
        # Current > baseline is by definition not a regression.
        verdict = evaluate_gate(
            current={
                "extraction": 1.0,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline={
                "extraction": 0.95,
                "evidence_retrieval": 0.9,
                "citations": 0.92,
                "refusal": 0.95,
                "missing_data": 0.95,
            },
            config=_config(),
        )
        assert verdict.passed is True

    def test_baseline_missing_category_skips_regression_check(self) -> None:
        # If a category isn't in baseline (new category, fresh suite),
        # only the absolute-floor check applies — no regression to
        # measure against.
        verdict = evaluate_gate(
            current={
                "extraction": 0.95,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline={
                # extraction not present
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            config=_config(),
        )
        assert verdict.passed is True


# ---------------------------------------------------------------------------
# Missing-category surface
# ---------------------------------------------------------------------------


class TestEvaluateGateMissingCategory:
    def test_required_category_absent_from_current_fails(self) -> None:
        # If the suite produced no cases for a category that has a
        # configured threshold, surface that as a violation rather than
        # silently passing.
        verdict = evaluate_gate(
            current={
                "extraction": 1.0,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                # refusal missing
                "missing_data": 1.0,
            },
            baseline=_baseline_all_one(),
            config=_config(),
            required_categories=("refusal",),
        )
        assert verdict.passed is False
        assert any(
            v.kind is ViolationKind.MISSING_CATEGORY and v.category == "refusal"
            for v in verdict.violations
        )


# ---------------------------------------------------------------------------
# Baseline file
# ---------------------------------------------------------------------------


class TestBaselineFile:
    def test_baseline_path_is_under_eval_baselines(self) -> None:
        # Sanity: BASELINE_PATH points at sidecar/tests/eval/baselines/week2.json
        assert BASELINE_PATH.name == "week2.json"
        assert BASELINE_PATH.parent.name == "baselines"

    def test_baseline_file_exists_and_parses(self) -> None:
        # The repo ships an initial baseline JSON.
        baseline = load_baseline()
        assert isinstance(baseline, dict)
        # Five W2 categories present
        for category in (
            "extraction",
            "evidence_retrieval",
            "citations",
            "refusal",
            "missing_data",
        ):
            assert category in baseline
            assert 0.0 <= baseline[category] <= 1.0

    def test_load_baseline_with_explicit_path(self, tmp_path: Path) -> None:
        target = tmp_path / "x.json"
        target.write_text(json.dumps({"extraction": 0.9}), encoding="utf-8")
        baseline = load_baseline(target)
        assert baseline == {"extraction": 0.9}


# ---------------------------------------------------------------------------
# CLI / non-zero exit
# ---------------------------------------------------------------------------


class TestRunGateCli:
    def test_pass_returns_exit_code_zero(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(_baseline_all_one()), encoding="utf-8")
        results_path = tmp_path / "results.json"
        # Current pass rates that beat both gates.
        current = {
            "extraction": 1.0,
            "evidence_retrieval": 1.0,
            "citations": 1.0,
            "refusal": 1.0,
            "missing_data": 1.0,
        }
        exit_code = run_gate_cli(
            current_rates=current,
            baseline_path=baseline,
            results_path=results_path,
            config=_config(),
        )
        assert exit_code == 0
        # Results file is written
        assert results_path.is_file()
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert payload["violations"] == []

    def test_fail_returns_non_zero(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(_baseline_all_one()), encoding="utf-8")
        results_path = tmp_path / "results.json"
        # Below absolute floor on extraction.
        current = {
            "extraction": 0.5,
            "evidence_retrieval": 1.0,
            "citations": 1.0,
            "refusal": 1.0,
            "missing_data": 1.0,
        }
        exit_code = run_gate_cli(
            current_rates=current,
            baseline_path=baseline,
            results_path=results_path,
            config=_config(),
        )
        assert exit_code != 0
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert len(payload["violations"]) >= 1


# ---------------------------------------------------------------------------
# Violation shape
# ---------------------------------------------------------------------------


class TestViolation:
    def test_violation_carries_category_kind_current_baseline_threshold(
        self,
    ) -> None:
        violation = Violation(
            category="extraction",
            kind=ViolationKind.BELOW_THRESHOLD,
            current=0.85,
            baseline=1.0,
            threshold=0.9,
            drop=0.15,
        )
        assert violation.category == "extraction"
        assert violation.kind is ViolationKind.BELOW_THRESHOLD
        assert violation.current == 0.85
