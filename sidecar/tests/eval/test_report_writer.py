"""Tests for generate_report() in EvalRunner (week1-gaps Task #19)."""

from __future__ import annotations

import datetime

import pytest

from tests.eval.harness import EvalCase, EvalCategory, EvalResult
from tests.eval.runner import generate_report


def _result(
    case_id: str,
    grounded: bool = True,
    behavior_pass: bool = True,
    grounding_failures: tuple = (),
) -> EvalResult:
    return EvalResult(
        case_id=case_id,
        grounded=grounded,
        grounding_failures=grounding_failures,
        behavior_pass=behavior_pass,
        citations_found=0,
    )


def _case(
    case_id: str,
    category: EvalCategory = EvalCategory.HAPPY_PATH,
    query: str = "What are the active problems?",
) -> EvalCase:
    return EvalCase(
        id=case_id,
        category=category,
        patient_id=8,
        query=query,
        expected_behavior="provides grounded summary",
    )


class TestGenerateReportStructure:
    def test_returns_string(self) -> None:
        report = generate_report([], [])
        assert isinstance(report, str)

    def test_contains_title(self) -> None:
        report = generate_report([], [])
        assert "# Eval Report" in report

    def test_contains_date(self) -> None:
        today = str(datetime.date.today())
        report = generate_report([], [])
        assert today in report

    def test_contains_summary_section(self) -> None:
        report = generate_report([], [])
        assert "## Summary" in report

    def test_contains_results_section(self) -> None:
        report = generate_report([], [])
        assert "## Results" in report


class TestGenerateReportCounts:
    def test_total_count_in_summary(self) -> None:
        results = [_result("a"), _result("b")]
        cases = [_case("a"), _case("b")]
        report = generate_report(results, cases)
        assert "Total: 2" in report

    def test_passed_count_in_summary(self) -> None:
        results = [_result("a"), _result("b", grounded=False)]
        cases = [_case("a"), _case("b")]
        report = generate_report(results, cases)
        assert "Passed: 1" in report

    def test_failed_count_in_summary(self) -> None:
        results = [_result("a"), _result("b", grounded=False)]
        cases = [_case("a"), _case("b")]
        report = generate_report(results, cases)
        assert "Failed: 1" in report

    def test_zero_counts_for_empty_input(self) -> None:
        report = generate_report([], [])
        assert "Total: 0" in report
        assert "Passed: 0" in report
        assert "Failed: 0" in report


class TestGenerateReportCaseLines:
    def test_passing_case_shows_pass_marker(self) -> None:
        results = [_result("hp_1")]
        cases = [_case("hp_1")]
        report = generate_report(results, cases)
        assert "PASS" in report or "✅" in report

    def test_failing_case_shows_fail_marker(self) -> None:
        results = [_result("hp_1", grounded=False)]
        cases = [_case("hp_1")]
        report = generate_report(results, cases)
        assert "FAIL" in report or "❌" in report

    def test_case_id_appears_in_report(self) -> None:
        results = [_result("my_case_001")]
        cases = [_case("my_case_001")]
        report = generate_report(results, cases)
        assert "my_case_001" in report

    def test_query_snippet_appears_in_report(self) -> None:
        results = [_result("c1")]
        cases = [_case("c1", query="What medications is the patient on?")]
        report = generate_report(results, cases)
        assert "What medications" in report

    def test_failure_reason_shown_for_failing_case(self) -> None:
        results = [_result("bad", grounded=False)]
        cases = [_case("bad")]
        report = generate_report(results, cases)
        lines = report.splitlines()
        fail_idx = next(i for i, l in enumerate(lines) if "bad" in l)
        # At least one line after the case line should explain the failure.
        context = "\n".join(lines[fail_idx : fail_idx + 5])
        assert "ungrounded" in context.lower() or "grounding" in context.lower()

    def test_no_failure_reason_for_passing_case(self) -> None:
        results = [_result("good")]
        cases = [_case("good")]
        report = generate_report(results, cases)
        assert "ungrounded" not in report.lower()


class TestGenerateReportByCategory:
    def test_category_heading_appears(self) -> None:
        results = [_result("h1")]
        cases = [_case("h1", category=EvalCategory.HALLUCINATION)]
        report = generate_report(results, cases)
        assert "hallucination" in report.lower()

    def test_multiple_categories_each_appear(self) -> None:
        results = [_result("h1"), _result("hp1")]
        cases = [
            _case("h1", category=EvalCategory.HALLUCINATION),
            _case("hp1", category=EvalCategory.HAPPY_PATH),
        ]
        report = generate_report(results, cases)
        assert "hallucination" in report.lower()
        assert "happy_path" in report.lower()

    def test_cases_grouped_under_correct_category(self) -> None:
        results = [_result("h1"), _result("hp1")]
        cases = [
            _case("h1", category=EvalCategory.HALLUCINATION),
            _case("hp1", category=EvalCategory.HAPPY_PATH),
        ]
        report = generate_report(results, cases)
        hall_pos = report.lower().index("hallucination")
        happy_pos = report.lower().index("happy_path")
        h1_pos = report.index("h1")
        hp1_pos = report.index("hp1")
        assert hall_pos < h1_pos < happy_pos or happy_pos < hp1_pos < hall_pos
