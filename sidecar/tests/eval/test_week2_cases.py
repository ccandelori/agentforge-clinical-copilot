"""Pytest wrapper for the Week 2 eval-case validator.

This test runs :func:`scripts.validate_eval_cases.validate` against the
``tests/eval/cases/week2/`` directory and asserts the suite parses
cleanly with the per-category distribution required by Task 16
(extraction=12, evidence_retrieval=10, citations=10, refusal=8,
missing_data=10 — 50 cases total).

The validator is the source of truth for the distribution check; this
test exists so the validator runs in CI as part of the normal pytest
sweep without anyone having to remember a separate command.
"""

from __future__ import annotations

import pathlib
import sys

# Ensure the sidecar root is importable so ``scripts.validate_eval_cases``
# resolves when pytest collects this test file directly.
_SIDECAR_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(_SIDECAR_ROOT))

from scripts.validate_eval_cases import (  # noqa: E402
    EXPECTED_DISTRIBUTION,
    EXPECTED_TOTAL,
    validate,
)


class TestWeek2EvalSuite:
    def test_directory_exists(self) -> None:
        """The week2 cases directory is required to exist."""
        from scripts.validate_eval_cases import WEEK2_CASES_DIR

        assert WEEK2_CASES_DIR.is_dir(), (
            f"week2 cases directory missing: {WEEK2_CASES_DIR}"
        )

    def test_validator_passes_on_full_suite(self) -> None:
        """Validator returns ok=True with no parse / distribution errors."""
        result = validate()
        assert result.ok, result.error_summary

    def test_total_case_count_is_fifty(self) -> None:
        result = validate()
        assert result.total == EXPECTED_TOTAL, (
            f"expected {EXPECTED_TOTAL} cases, got {result.total}"
        )

    def test_distribution_matches_spec(self) -> None:
        """Per-category counts match the Task 16 acceptance criterion."""
        result = validate()
        for category, expected in EXPECTED_DISTRIBUTION.items():
            actual = result.counts_by_category.get(category.value, 0)
            assert actual == expected, (
                f"{category.value}: expected {expected}, got {actual}"
            )

    def test_case_ids_are_unique_across_suite(self) -> None:
        result = validate()
        assert not result.duplicate_ids, (
            f"duplicate case ids: {', '.join(result.duplicate_ids)}"
        )
