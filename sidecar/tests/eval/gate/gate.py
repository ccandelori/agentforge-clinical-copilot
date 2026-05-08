"""Eval-gate verdict logic (Task 18.4).

The gate sits on top of the pass-rate dict the runner + scoring
modules produce.  Two failure modes:

  1. Absolute-floor — any category below ``category_thresholds[<cat>]``
     in the config (defaults to 0.9 across the board).
  2. Regression — any category whose pass rate dropped by more than
     ``regression_threshold`` (default 5 %) compared to the pinned
     baseline.

A third surface, ``MISSING_CATEGORY``, fails the gate when a category
the caller declared required is absent from the current run.  This
prevents a case-loading regression (e.g. someone deletes a yaml file)
from sliding through unnoticed.

The CLI surface ``run_gate_cli`` writes a JSON results file for the
diff reporter (Task 18.5) and returns a process exit code (0 on pass,
1 on any violation).  Wiring into actual CI is Tasks 20 + 22.

Module category mapping note
----------------------------
The config's ``category_thresholds`` keys are the W2 *metric* names —
``schema_valid``, ``citation_present``, ``factually_consistent``,
``safe_refusal``, ``no_phi_in_logs``.  The runner's pass-rate dict keys
are the W2 *case-category* names — ``extraction``,
``evidence_retrieval``, ``citations``, ``refusal``, ``missing_data``.

These name spaces are different by design (metrics describe the
contract being checked; case categories describe the suite slice the
case author tagged).  ``CATEGORY_THRESHOLD_MAP`` keeps the wiring
explicit so a future re-shape of either side stays a single-line fix.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


# Floating-point safety net for the regression-drop comparison.
# ``1.0 - 0.95`` is ``0.050000000000000044`` in IEEE 754 — without a
# tolerance the gate would incorrectly flag an exactly-at-threshold drop
# as a regression. The tolerance is small enough that real regressions
# (cents on the dollar of pass-rate) still trip it.
_DROP_EPSILON: Final[float] = 1e-9

from tests.eval.gate.config import EvalGateConfig


# Mapping from runner pass-rate category → config-threshold key.
# Documented above; one place to look when either side moves.
CATEGORY_THRESHOLD_MAP: Final[dict[str, str]] = {
    "extraction": "schema_valid",
    "evidence_retrieval": "citation_present",
    "citations": "citation_present",
    "refusal": "safe_refusal",
    "missing_data": "factually_consistent",
}


# Sidecar-relative path: this file is at sidecar/tests/eval/gate/gate.py
# so the baseline lives at sidecar/tests/eval/baselines/week2.json
BASELINE_PATH: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parent.parent / "baselines" / "week2.json"
)


class ViolationKind(StrEnum):
    """Closed set of gate-failure shapes."""

    BELOW_THRESHOLD = "below_threshold"
    REGRESSION = "regression"
    MISSING_CATEGORY = "missing_category"


@dataclass(frozen=True)
class Violation:
    """One per-category gate violation.

    ``current`` / ``baseline`` are the pass-rate snapshots the violation
    was computed from.  ``threshold`` is the absolute floor (only
    populated for ``BELOW_THRESHOLD``).  ``drop`` is the regression
    magnitude (only populated for ``REGRESSION``).
    """

    category: str
    kind: ViolationKind
    current: float | None = None
    baseline: float | None = None
    threshold: float | None = None
    drop: float | None = None

    def to_dict(self) -> dict[str, str | float | None]:
        """Serialize to a JSON-safe dict for the results file."""
        return {
            "category": self.category,
            "kind": self.kind.value,
            "current": self.current,
            "baseline": self.baseline,
            "threshold": self.threshold,
            "drop": self.drop,
        }


@dataclass(frozen=True)
class GateVerdict:
    """The gate's verdict for one run."""

    passed: bool
    violations: tuple[Violation, ...]


def evaluate_gate(
    *,
    current: Mapping[str, float],
    baseline: Mapping[str, float],
    config: EvalGateConfig,
    required_categories: tuple[str, ...] = (),
) -> GateVerdict:
    """Compute the gate's verdict from per-category pass rates.

    Parameters
    ----------
    current:
        Per-category pass rates from the just-completed eval run.
    baseline:
        Per-category pass rates from the pinned baseline JSON.
    config:
        Loaded :class:`EvalGateConfig` carrying thresholds.
    required_categories:
        Categories the caller declares must be present in ``current``.
        Absent categories surface as ``MISSING_CATEGORY`` violations.
    """
    violations: list[Violation] = []

    # Missing-category check first — a missing category short-circuits
    # the per-category arithmetic for that name.
    for required in required_categories:
        if required not in current:
            violations.append(
                Violation(
                    category=required,
                    kind=ViolationKind.MISSING_CATEGORY,
                )
            )

    for category, current_rate in current.items():
        # Absolute-floor check.
        threshold_key = CATEGORY_THRESHOLD_MAP.get(category)
        if threshold_key is not None:
            threshold = config.category_thresholds.get(threshold_key)
            if threshold is not None and current_rate < threshold:
                violations.append(
                    Violation(
                        category=category,
                        kind=ViolationKind.BELOW_THRESHOLD,
                        current=current_rate,
                        threshold=threshold,
                    )
                )

        # Regression check — only when baseline carries the category.
        if category in baseline:
            baseline_rate = baseline[category]
            drop = baseline_rate - current_rate
            if drop > config.regression_threshold + _DROP_EPSILON:
                violations.append(
                    Violation(
                        category=category,
                        kind=ViolationKind.REGRESSION,
                        current=current_rate,
                        baseline=baseline_rate,
                        drop=drop,
                    )
                )

    return GateVerdict(passed=not violations, violations=tuple(violations))


def load_baseline(
    path: pathlib.Path | str | None = None,
) -> dict[str, float]:
    """Load and parse the pinned baseline JSON.

    Returns a flat ``{category: pass_rate}`` dict.  Keys beginning with
    underscore are reserved for metadata (``_meta`` carries the regen
    provenance — see ``baselines/week2.json``) and are skipped during
    parsing rather than treated as a category. Missing or malformed
    JSON raises with a descriptive error.
    """
    target = pathlib.Path(path) if path is not None else BASELINE_PATH
    if not target.is_file():
        raise FileNotFoundError(f"baseline file not found: {target}")
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"baseline must be a JSON object; got {type(raw).__name__}"
        )
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"baseline key must be a string; got {key!r}")
        if key.startswith("_"):
            # Metadata key — not a category pass rate.
            continue
        if not isinstance(value, (int, float)):
            raise ValueError(
                f"baseline[{key!r}] must be numeric; got {value!r}"
            )
        parsed[key] = float(value)
    return parsed


def run_gate_cli(
    *,
    current_rates: Mapping[str, float],
    baseline_path: pathlib.Path,
    results_path: pathlib.Path,
    config: EvalGateConfig,
    required_categories: tuple[str, ...] = (),
) -> int:
    """CLI-style entry point for the gate.

    Loads the baseline, computes the verdict, writes a JSON results
    file, and returns a process exit code (0 on pass, 1 on any
    violation).  The diff reporter (Task 18.5) reads ``results_path``
    to render the markdown comment for CI.
    """
    baseline = load_baseline(baseline_path)
    verdict = evaluate_gate(
        current=current_rates,
        baseline=baseline,
        config=config,
        required_categories=required_categories,
    )
    payload = {
        "passed": verdict.passed,
        "violations": [v.to_dict() for v in verdict.violations],
        "current": dict(current_rates),
        "baseline": baseline,
    }
    results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if verdict.passed else 1


__all__ = (
    "BASELINE_PATH",
    "CATEGORY_THRESHOLD_MAP",
    "GateVerdict",
    "Violation",
    "ViolationKind",
    "evaluate_gate",
    "load_baseline",
    "run_gate_cli",
)
