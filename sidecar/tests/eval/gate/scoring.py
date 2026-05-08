"""Per-category pass-rate aggregator (Task 18.3).

The runner returns a flat list of :class:`W2RunnerResult`. The gate
logic needs ``{category: passed/total}``. Splitting that reduce step
into its own module keeps the gate-logic tests independent of the
runner — they can synthesise ``W2RunnerResult`` objects directly and
verify the gate's threshold + regression arithmetic without paying
for a supervisor / harness round trip.

Output shape:
    Categories absent from the input results are absent from the
    output dict. Callers that need "every required category must be
    present" semantics layer that check on top — the reducer itself is
    a pure aggregation, no policy.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from tests.eval.gate.runner_w2 import W2RunnerResult


def summarize_by_category(
    results: Iterable[W2RunnerResult],
) -> dict[str, float]:
    """Aggregate per-category pass rates as ``passed/total``.

    Empty input returns an empty dict — the gate logic handles the
    "no results" case explicitly (it's a config error, not a runner
    state).
    """
    materialised = list(results)
    if not materialised:
        return {}
    totals: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    for result in materialised:
        category = result.case.category.value
        totals[category] += 1
        if result.eval_result.passed:
            passed[category] += 1
    return {
        category: passed[category] / totals[category] for category in totals
    }


__all__ = ("summarize_by_category",)
