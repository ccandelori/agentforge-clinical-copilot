"""Markdown diff reporter for the eval-gate (Task 18.5).

CI pastes the rendered output as an MR/PR comment. The report has
three sections:

  * Heading + summary (PASS/FAIL marker, violation count).
  * Per-category table (Category | Baseline | Current | Delta | Status).
  * Violation details, one block per gate failure (only when
    ``verdict.passed`` is False).

The table columns are fixed and human-skimmable; a future script
parsing the report can rely on the column order as a contract.

Categories absent from the baseline render as ``new`` in the Baseline
column (rather than a misleading 0.0). Categories absent from the
current run render with a missing-value marker so a case-loading
regression doesn't slip through silently.
"""

from __future__ import annotations

from collections.abc import Mapping

from tests.eval.gate.config import EvalGateConfig
from tests.eval.gate.gate import GateVerdict, Violation, ViolationKind


_NA: str = "—"


def render_diff_report(
    *,
    verdict: GateVerdict,
    current: Mapping[str, float],
    baseline: Mapping[str, float],
    config: EvalGateConfig,
) -> str:
    """Render a markdown diff report for the gate's verdict.

    The output is meant to be pasted into a CI MR/PR comment, so the
    first character is a markdown heading (``#``) — review systems
    anchor on the heading for collapse / pinning.
    """
    lines: list[str] = []
    lines.append("# Eval Gate Report")
    lines.append("")
    lines.append(_summary_line(verdict))
    lines.append(
        f"- regression threshold: {config.regression_threshold:.2f}"
    )
    lines.append("")
    lines.append("## Per-category pass rates")
    lines.append("")
    lines.extend(_table_lines(current, baseline, verdict))
    lines.append("")
    if not verdict.passed and verdict.violations:
        lines.append("## Violations")
        lines.append("")
        for violation in verdict.violations:
            lines.extend(_violation_block(violation))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summary_line(verdict: GateVerdict) -> str:
    """One-line PASS/FAIL summary with violation count."""
    marker = "PASS" if verdict.passed else "FAIL"
    count = len(verdict.violations)
    plural = "s" if count != 1 else ""
    return f"- status: **{marker}** ({count} violation{plural})"


def _table_lines(
    current: Mapping[str, float],
    baseline: Mapping[str, float],
    verdict: GateVerdict,
) -> list[str]:
    """Render the per-category markdown table.

    Builds the row set from the union of ``current`` and ``baseline``
    keys so a category present on either side gets surfaced.
    """
    categories = sorted(set(current) | set(baseline))
    lines = [
        "| Category | Baseline | Current | Delta | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    violation_index = _violation_index(verdict)
    for category in categories:
        baseline_value = baseline.get(category)
        current_value = current.get(category)
        baseline_cell = (
            "new" if baseline_value is None else f"{baseline_value:.2f}"
        )
        current_cell = (
            _NA if current_value is None else f"{current_value:.2f}"
        )
        delta_cell = _delta_cell(current_value, baseline_value)
        status_cell = _status_cell(category, violation_index)
        lines.append(
            f"| {category} | {baseline_cell} | {current_cell} | "
            f"{delta_cell} | {status_cell} |"
        )
    return lines


def _delta_cell(
    current: float | None, baseline: float | None
) -> str:
    """Render the Delta cell with a direction marker."""
    if current is None or baseline is None:
        return _NA
    delta = current - baseline
    if abs(delta) < 1e-9:
        return "0.00"
    arrow = "+" if delta > 0 else "-"
    return f"{arrow}{abs(delta):.2f}"


def _violation_index(
    verdict: GateVerdict,
) -> dict[str, list[ViolationKind]]:
    """Group violations by category for fast row-status lookup."""
    index: dict[str, list[ViolationKind]] = {}
    for violation in verdict.violations:
        index.setdefault(violation.category, []).append(violation.kind)
    return index


def _status_cell(
    category: str, violation_index: Mapping[str, list[ViolationKind]]
) -> str:
    """Render the Status cell: PASS or a comma-separated list of kinds."""
    kinds = violation_index.get(category)
    if not kinds:
        return "PASS"
    return "FAIL: " + ", ".join(k.value for k in kinds)


def _violation_block(violation: Violation) -> list[str]:
    """Render the per-violation detail block.

    One bullet per violation, naming the kind, category, and the
    relevant magnitudes (threshold for BELOW_THRESHOLD, drop for
    REGRESSION).
    """
    header = f"- **{violation.kind.value}** — `{violation.category}`"
    detail_parts: list[str] = []
    if violation.current is not None:
        detail_parts.append(f"current: {violation.current:.2f}")
    if violation.baseline is not None:
        detail_parts.append(f"baseline: {violation.baseline:.2f}")
    if violation.threshold is not None:
        detail_parts.append(f"threshold: {violation.threshold:.2f}")
    if violation.drop is not None:
        detail_parts.append(f"drop: {violation.drop:.2f}")
    if detail_parts:
        return [header, "  - " + "; ".join(detail_parts)]
    return [header]


__all__ = ("render_diff_report",)
