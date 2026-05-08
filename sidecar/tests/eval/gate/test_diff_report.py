"""Tests for the eval-gate diff reporter (Task 18.5).

The reporter takes the gate's verdict + per-category baseline/current
pass rates and produces a markdown table CI pastes as an MR/PR
comment. The table is human-skimmable (PASS / FAIL marker per row,
arrows for direction of change) and parseable enough that a future
script can extract the numbers programmatically (one row per
category, fixed columns).

Tests cover:
  * Pass case — single "all-green" summary line + per-category table.
  * Fail case — violations enumerated below the table, each violation
    naming the kind, category, current rate, baseline rate.
  * Empty / new-category handling — current categories absent from
    baseline render a "new" badge instead of a baseline value.
"""

from __future__ import annotations

import re

from tests.eval.gate.config import EvalGateConfig
from tests.eval.gate.diff_report import render_diff_report
from tests.eval.gate.gate import GateVerdict, Violation, ViolationKind


def _config() -> EvalGateConfig:
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


# ---------------------------------------------------------------------------
# Pass case
# ---------------------------------------------------------------------------


class TestRenderDiffReportPass:
    def test_pass_includes_all_clear_summary_line(self) -> None:
        verdict = GateVerdict(passed=True, violations=())
        report = render_diff_report(
            verdict=verdict,
            current={
                "extraction": 1.0,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            baseline={
                "extraction": 1.0,
                "evidence_retrieval": 1.0,
                "citations": 1.0,
                "refusal": 1.0,
                "missing_data": 1.0,
            },
            config=_config(),
        )
        assert "PASS" in report
        # Summary text mentions zero violations on the pass path.
        assert re.search(r"0 violations?", report, flags=re.IGNORECASE)

    def test_pass_renders_one_table_row_per_category(self) -> None:
        verdict = GateVerdict(passed=True, violations=())
        current = {
            "extraction": 1.0,
            "evidence_retrieval": 0.95,
            "citations": 0.92,
            "refusal": 1.0,
            "missing_data": 1.0,
        }
        baseline = {k: 1.0 for k in current}
        report = render_diff_report(
            verdict=verdict,
            current=current,
            baseline=baseline,
            config=_config(),
        )
        for category in current:
            assert category in report

    def test_pass_table_has_markdown_structure(self) -> None:
        verdict = GateVerdict(passed=True, violations=())
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 1.0},
            baseline={"extraction": 1.0},
            config=_config(),
        )
        # Markdown table separator row (---).
        assert "| ---" in report or "|---" in report
        # Header row contains the column names we rely on for parseability.
        assert "Category" in report
        assert "Baseline" in report
        assert "Current" in report
        assert "Delta" in report


# ---------------------------------------------------------------------------
# Fail case
# ---------------------------------------------------------------------------


class TestRenderDiffReportFail:
    def test_fail_summary_lists_violation_count(self) -> None:
        violation = Violation(
            category="extraction",
            kind=ViolationKind.BELOW_THRESHOLD,
            current=0.5,
            threshold=0.9,
        )
        verdict = GateVerdict(passed=False, violations=(violation,))
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 0.5},
            baseline={"extraction": 1.0},
            config=_config(),
        )
        assert "FAIL" in report
        assert re.search(r"1 violation", report, flags=re.IGNORECASE)

    def test_fail_emits_one_violation_block_per_violation(self) -> None:
        violations = (
            Violation(
                category="extraction",
                kind=ViolationKind.BELOW_THRESHOLD,
                current=0.5,
                threshold=0.9,
            ),
            Violation(
                category="refusal",
                kind=ViolationKind.REGRESSION,
                current=0.85,
                baseline=1.0,
                drop=0.15,
            ),
        )
        verdict = GateVerdict(passed=False, violations=violations)
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 0.5, "refusal": 0.85},
            baseline={"extraction": 1.0, "refusal": 1.0},
            config=_config(),
        )
        # Both violations surface in the report.
        assert "extraction" in report
        assert "refusal" in report
        # Both kinds are named.
        assert ViolationKind.BELOW_THRESHOLD.value in report
        assert ViolationKind.REGRESSION.value in report

    def test_below_threshold_violation_includes_threshold(self) -> None:
        violation = Violation(
            category="extraction",
            kind=ViolationKind.BELOW_THRESHOLD,
            current=0.5,
            threshold=0.9,
        )
        verdict = GateVerdict(passed=False, violations=(violation,))
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 0.5},
            baseline={"extraction": 1.0},
            config=_config(),
        )
        # Threshold is named so the human knows the floor.
        assert "0.9" in report or "0.90" in report

    def test_regression_violation_includes_drop(self) -> None:
        violation = Violation(
            category="refusal",
            kind=ViolationKind.REGRESSION,
            current=0.85,
            baseline=1.0,
            drop=0.15,
        )
        verdict = GateVerdict(passed=False, violations=(violation,))
        report = render_diff_report(
            verdict=verdict,
            current={"refusal": 0.85},
            baseline={"refusal": 1.0},
            config=_config(),
        )
        # Drop magnitude is named.
        assert "0.15" in report or "15" in report


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestRenderDiffReportEdges:
    def test_new_category_renders_baseline_as_new(self) -> None:
        # Current has 'extraction' that isn't in baseline → render a
        # marker so the human sees this is a fresh measurement, not a
        # regression.
        verdict = GateVerdict(passed=True, violations=())
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 1.0},
            baseline={},
            config=_config(),
        )
        # Lowercased keyword search: "new" appears as the baseline cell.
        assert "new" in report.lower()

    def test_dropped_category_renders_current_as_missing(self) -> None:
        # Baseline has 'evidence_retrieval' that current doesn't —
        # surface as a row with a clear marker rather than dropping
        # silently.
        verdict = GateVerdict(passed=False, violations=())
        report = render_diff_report(
            verdict=verdict,
            current={},
            baseline={"evidence_retrieval": 1.0},
            config=_config(),
        )
        assert "evidence_retrieval" in report
        # Marker for the missing current value.
        assert "—" in report or "missing" in report.lower() or "n/a" in report.lower()

    def test_report_has_a_top_level_heading(self) -> None:
        verdict = GateVerdict(passed=True, violations=())
        report = render_diff_report(
            verdict=verdict,
            current={"extraction": 1.0},
            baseline={"extraction": 1.0},
            config=_config(),
        )
        # Markdown H1 or H2 — CI comments need a heading anchor.
        assert report.lstrip().startswith("#")
