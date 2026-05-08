"""Validator for the Week 2 YAML eval-case suite.

Loads every ``.yaml`` file under ``tests/eval/cases/week2/`` via the
canonical :func:`load_yaml_cases` loader, asserts the per-category
distribution required by Task 16 (extraction=12, evidence_retrieval=10,
citations=10, refusal=8, missing_data=10 — 50 cases total), checks that
every case ``id`` is unique across the entire week2 suite, prints a
per-category summary, and exits non-zero on any failure.

The script is import-safe (no side effects when imported), exposes a
:func:`validate` entry point that returns a structured :class:`ValidationResult`
for use from a pytest case, and a :func:`main` CLI entry point that
prints a human-readable report and sets the process exit code.

Run from the sidecar/ directory::

    uv run python scripts/validate_eval_cases.py

Or import from a test::

    from scripts.validate_eval_cases import validate
    result = validate()
    assert result.ok, result.error_summary

The expected distribution lives in :data:`EXPECTED_DISTRIBUTION`. If
Task 16's spec changes, edit that constant rather than chasing the
counts through the assertion logic.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

# Allow running as a script (``python scripts/validate_eval_cases.py``)
# from the sidecar/ root *and* importing from a test (which already has
# the sidecar src + tests on sys.path). The src layout means
# ``tests.eval.harness`` resolves correctly only after we add the
# sidecar root to sys.path; pytest does that itself, but the CLI mode
# needs the explicit insertion.
_SIDECAR_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_SIDECAR_ROOT) not in sys.path:
    sys.path.insert(0, str(_SIDECAR_ROOT))

from tests.eval.harness import EvalCase, EvalCategory  # noqa: E402
from tests.eval.yaml_cases import load_yaml_cases  # noqa: E402

WEEK2_CASES_DIR: Final[pathlib.Path] = (
    _SIDECAR_ROOT / "tests" / "eval" / "cases" / "week2"
)

# Source of truth for Task 16 acceptance criterion #1.
EXPECTED_DISTRIBUTION: Final[dict[EvalCategory, int]] = {
    EvalCategory.EXTRACTION: 12,
    EvalCategory.EVIDENCE_RETRIEVAL: 10,
    EvalCategory.CITATIONS: 10,
    EvalCategory.REFUSAL: 8,
    EvalCategory.MISSING_DATA: 10,
}

EXPECTED_TOTAL: Final[int] = sum(EXPECTED_DISTRIBUTION.values())


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating the week2 case suite.

    ``ok`` is True iff the case set parses cleanly, the category
    distribution matches :data:`EXPECTED_DISTRIBUTION` exactly, and
    every case id is unique across the whole suite.
    """

    ok: bool
    cases: tuple[EvalCase, ...] = ()
    counts_by_category: dict[str, int] = field(default_factory=dict)
    duplicate_ids: tuple[str, ...] = ()
    distribution_errors: tuple[str, ...] = ()
    parse_errors: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def error_summary(self) -> str:
        """Human-readable error block; empty string if ``ok``."""
        if self.ok:
            return ""
        parts: list[str] = []
        if self.parse_errors:
            parts.append("Parse errors:")
            parts.extend(f"  - {msg}" for msg in self.parse_errors)
        if self.duplicate_ids:
            parts.append(f"Duplicate ids: {', '.join(self.duplicate_ids)}")
        if self.distribution_errors:
            parts.append("Distribution errors:")
            parts.extend(f"  - {msg}" for msg in self.distribution_errors)
        return "\n".join(parts)


def _yaml_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Return week2 ``.yaml`` files in deterministic (sorted) order."""
    return sorted(directory.glob("*.yaml"))


def validate(directory: pathlib.Path | None = None) -> ValidationResult:
    """Validate the week2 YAML case suite.

    Parameters
    ----------
    directory:
        Override the default ``tests/eval/cases/week2/`` location for
        testing. Defaults to :data:`WEEK2_CASES_DIR`.
    """
    target = directory or WEEK2_CASES_DIR
    parse_errors: list[str] = []
    cases: list[EvalCase] = []

    if not target.is_dir():
        return ValidationResult(
            ok=False,
            parse_errors=(f"week2 cases directory does not exist: {target}",),
        )

    for yaml_path in _yaml_files(target):
        try:
            cases.extend(load_yaml_cases(yaml_path))
        except Exception as exc:  # broad: surface every parse failure
            parse_errors.append(f"{yaml_path.name}: {exc}")

    counts = Counter(case.category.value for case in cases)
    counts_by_category = dict(counts)

    distribution_errors: list[str] = []
    for category, expected in EXPECTED_DISTRIBUTION.items():
        actual = counts.get(category.value, 0)
        if actual != expected:
            distribution_errors.append(
                f"{category.value}: expected {expected}, got {actual}"
            )

    # Surface unexpected categories so a typo doesn't silently inflate
    # an unrelated bucket.
    expected_values = {cat.value for cat in EXPECTED_DISTRIBUTION}
    for value, count in counts.items():
        if value not in expected_values:
            distribution_errors.append(
                f"unexpected category {value!r} (count {count})"
            )

    if len(cases) != EXPECTED_TOTAL and not distribution_errors:
        distribution_errors.append(
            f"total cases: expected {EXPECTED_TOTAL}, got {len(cases)}"
        )

    ids = [case.id for case in cases]
    duplicates = tuple(sorted({i for i in ids if ids.count(i) > 1}))

    ok = (
        not parse_errors
        and not duplicates
        and not distribution_errors
        and len(cases) == EXPECTED_TOTAL
    )

    return ValidationResult(
        ok=ok,
        cases=tuple(cases),
        counts_by_category=counts_by_category,
        duplicate_ids=duplicates,
        distribution_errors=tuple(distribution_errors),
        parse_errors=tuple(parse_errors),
    )


def _format_report(result: ValidationResult) -> str:
    """Compact, human-readable report suitable for CI logs."""
    lines: list[str] = []
    lines.append(f"Total cases loaded: {result.total} (expected {EXPECTED_TOTAL})")
    lines.append("Per-category counts:")
    for category, expected in EXPECTED_DISTRIBUTION.items():
        actual = result.counts_by_category.get(category.value, 0)
        marker = "OK" if actual == expected else "FAIL"
        lines.append(
            f"  [{marker}] {category.value}: {actual} / {expected}"
        )
    if result.ok:
        lines.append("\nAll checks passed.")
    else:
        lines.append("\nFAILURES:")
        lines.append(result.error_summary)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    del argv  # no flags yet; signature reserved for future --json etc.
    result = validate()
    print(_format_report(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
