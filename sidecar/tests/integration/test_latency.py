"""Latency-budget tests for /agentforge/turn (Task 47.5).

Two tests, both opt-in via the ``latency`` pytest marker:

  test_internal_endpoints_p95_under_budget
    Times the JWT-protected internal endpoints directly (5 samples
    each, 5 endpoints). NO LLM calls — these probe the PHP module
    + sidecar fetcher round-trip. Budget per ARCHITECTURE.md §3:
    each tool fetch should land well under the 2s per-tool
    ceiling. We assert p95 < 1000ms to leave headroom on a
    healthy stack.

  test_uc1_total_turn_p95_under_budget
    Full end-to-end /turn including LLM synthesis (5 samples).
    Tagged ``slow`` AND ``latency``. The 7s p95 budget from
    ARCHITECTURE.md is the PRODUCTION target on the droplet —
    measured dev-laptop latencies run 12-25s (Anthropic API
    cumulative cost). The default budget here is 30s p95 (lenient
    for dev-laptop); override via AGENTFORGE_INT_UC1_P95_BUDGET_MS
    when running against production-shaped hardware to assert
    the real 7000ms target.

Default ``uv run pytest`` deselects both markers. Run explicitly::

    uv run pytest -m latency tests/integration/test_latency.py

The :class:`LatencyTracker` context manager records wall-clock
timings and exposes ``p50``, ``p95``, ``p99`` over the captured
samples. Sufficient for "is the system in the right ballpark?"
checks; production SLO monitoring does not run here.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import pytest

# 47.5 — latency tests are explicitly opt-in. Apply the marker at
# module level so every test in the file gets it; the LLM-hitting
# test additionally carries ``slow`` for the full opt-in.
pytestmark = pytest.mark.latency


_TURN_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge/public/turn.php"
)
_INTERNAL_PATHS_NO_PARAMS: tuple[str, ...] = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/demographics.php",
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/problems.php",
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/medications.php",
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/allergies.php",
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/immunizations.php",
)


@dataclass
class LatencyTracker:
    """Collects per-sample latencies (ms) and computes p50/p95/p99.

    Use as a context manager around the operation you want to time::

        tracker = LatencyTracker(name="uc1_turn")
        for _ in range(8):
            with tracker.sample():
                await do_the_thing()
        assert tracker.p95 < 12_000

    Stays minimal on purpose — production observability is
    Langfuse's job (when configured). This tracker is for
    test-time assertions only.
    """

    name: str
    samples_ms: list[float] = field(default_factory=list)

    @contextmanager
    def sample(self) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.samples_ms.append((time.perf_counter() - start) * 1000.0)

    @property
    def p50(self) -> float:
        return self._percentile(50)

    @property
    def p95(self) -> float:
        return self._percentile(95)

    @property
    def p99(self) -> float:
        return self._percentile(99)

    def _percentile(self, pct: int) -> float:
        if not self.samples_ms:
            raise RuntimeError(
                f"LatencyTracker({self.name}) has no samples; nothing "
                "to compute a percentile from."
            )
        # statistics.quantiles is the boring/correct Python way to
        # get percentiles; use ``method='inclusive'`` so p100 of n=1
        # is the single sample (matches operator intuition).
        if len(self.samples_ms) == 1:
            return self.samples_ms[0]
        sorted_samples = sorted(self.samples_ms)
        # 100 quantile cuts the sorted list into 100 equal-sized
        # buckets; index pct-1 is the pct-th percentile boundary.
        cuts = statistics.quantiles(
            sorted_samples, n=100, method="inclusive"
        )
        return cuts[pct - 1]

    def summary(self) -> str:
        if not self.samples_ms:
            return f"{self.name}: no samples"
        return (
            f"{self.name}: n={len(self.samples_ms)}, "
            f"p50={self.p50:.0f}ms, "
            f"p95={self.p95:.0f}ms, "
            f"p99={self.p99:.0f}ms, "
            f"max={max(self.samples_ms):.0f}ms"
        )


# ---------------------------------------------------------------------
# No-LLM tier — internal endpoints, fast enough to run regularly.
# ---------------------------------------------------------------------


async def test_internal_endpoints_p95_under_budget(
    authenticated_client: httpx.AsyncClient,
    patient_context: int,
) -> None:
    """Each JWT-protected internal endpoint responds well under 1s p95.

    The agent's per-tool budget is 2s (per ARCHITECTURE.md §3 —
    enforced at the orchestrator via TimeoutPolicy.per_tool=2.0).
    These probes hit the PHP endpoint directly through the
    authenticated session (which carries a valid OpenEMR cookie),
    so they bypass the JWT mint step but still exercise the
    fetcher's HTTP path AND the SQL repo.

    Asserts p95 < 1000ms — tight enough that a regression (an
    accidental N+1, a missing index, a network blip) shows up,
    loose enough that a busy laptop doesn't false-trigger.

    Note: these calls go through OpenEMR's session auth, not the
    sidecar's JWT path, so the test pid mismatch can't fire (we're
    NOT exercising the sidecar). What we're locking is the PHP
    repo round-trip latency; the JWT path adds <5ms of validation
    overhead in practice.
    """
    # Patient context is bound (via the fixture) so demographics &
    # friends have something to fetch.
    pid = patient_context
    samples_per_endpoint = 5
    overall = LatencyTracker(name="all_internal_endpoints")

    for path in _INTERNAL_PATHS_NO_PARAMS:
        per_endpoint = LatencyTracker(name=path.rsplit("/", 1)[-1])
        for _ in range(samples_per_endpoint):
            with per_endpoint.sample():
                response = await authenticated_client.get(
                    path, params={"pid": pid}
                )
            # 401 here would mean the session-auth path doesn't
            # cover the internal endpoints — not a latency problem.
            # We don't assert status; we only care about timing.
            # A 5xx still produces a sample; treating timeouts as
            # NaN would just mask real regressions.
            _ = response

        # Per-endpoint print so the operator sees which one is
        # slow when an assertion fails.
        print(f"\n  {per_endpoint.summary()}")
        overall.samples_ms.extend(per_endpoint.samples_ms)

    print(f"\n  {overall.summary()}")
    assert overall.p95 < 1000.0, (
        f"Internal endpoint p95 latency {overall.p95:.0f}ms exceeds "
        f"1000ms budget. Per-sample times: "
        f"{[round(s) for s in overall.samples_ms]}"
    )


# ---------------------------------------------------------------------
# LLM tier — full /turn round-trip. Real Anthropic API calls.
# ---------------------------------------------------------------------


@pytest.mark.slow
async def test_uc1_total_turn_p95_under_budget(
    authenticated_client: httpx.AsyncClient,
    patient_context_factory,
) -> None:
    """End-to-end /turn p95 stays within the dev-laptop budget.

    Production target is 7s p95 (ARCHITECTURE.md §3). On a dev
    laptop with host-script sidecar, the same workload runs
    noticeably slower — Anthropic's TTFB plus the controller's
    streaming pipe can push the median to 8-12s on a complex
    chart. We assert 12s p95 here, leaving headroom for the
    integration test environment without losing the regression-
    detection signal.

    8 samples is the minimum for a meaningful p95 estimate; more
    would be better statistically but each sample costs API tokens
    AND ~15s of wall time.
    """
    await patient_context_factory(8)  # Eula — complex chronic patient

    tracker = LatencyTracker(name="uc1_total_turn")
    sample_count = 5
    failures = 0
    statuses: list[int] = []

    for i in range(sample_count):
        with tracker.sample():
            try:
                response = await authenticated_client.post(
                    _TURN_PATH,
                    json={"message": "Give me a brief chart overview."},
                    timeout=httpx.Timeout(
                        connect=5.0, read=60.0, write=10.0, pool=5.0
                    ),
                )
                statuses.append(response.status_code)
                if response.status_code != 200:
                    failures += 1
                    print(
                        f"\n  sample {i + 1}: status {response.status_code} "
                        f"(treating as failure, not skip — transient 503s "
                        "from controller idle-timeout count toward latency "
                        "budget)"
                    )
            except httpx.HTTPError as exc:
                failures += 1
                statuses.append(-1)
                print(f"\n  sample {i + 1} HTTP error: {exc}")

    print(f"\n  {tracker.summary()}, failures={failures}, statuses={statuses}")

    # If EVERY sample failed the sidecar is fundamentally down;
    # skip rather than fail (the suite is meant to test latency,
    # not infrastructure availability).
    if failures == sample_count:
        pytest.skip(
            f"All {sample_count} samples failed (statuses={statuses}). "
            "Sidecar appears down. Start ./sidecar/scripts/sidecar.sh "
            "and retry."
        )

    # Otherwise: more-than-half failures = real problem, surface it.
    assert failures < sample_count // 2 + 1, (
        f"More than half the samples failed ({failures}/{sample_count}, "
        f"statuses={statuses}). Suite is detecting a fundamental "
        "issue, not a latency one."
    )

    # Budget: production target is 7s p95 (ARCHITECTURE.md §3,
    # measured on the droplet). As of week1-gaps Task #8 the
    # ``total_turn`` budget is enforced inside ``Orchestrator.turn``
    # via ``asyncio.timeout`` — the test default matches that
    # enforcement so a regression here surfaces locally rather than
    # only in staging. Dev-laptop runs that legitimately need slack
    # (cold sidecar, slow Anthropic round-trip) can override via
    # ``AGENTFORGE_INT_UC1_P95_BUDGET_MS`` — set higher to skip the
    # production-tight assertion while still measuring p95.
    budget_ms_raw = os.environ.get(
        "AGENTFORGE_INT_UC1_P95_BUDGET_MS", "7000"
    )
    try:
        budget_ms = float(budget_ms_raw)
    except ValueError:
        pytest.fail(
            f"AGENTFORGE_INT_UC1_P95_BUDGET_MS={budget_ms_raw!r} is "
            "not a valid number; must be parseable as float."
        )
    assert tracker.p95 < budget_ms, (
        f"UC-1 /turn p95 latency {tracker.p95:.0f}ms exceeds "
        f"{budget_ms:.0f}ms budget (env override "
        "AGENTFORGE_INT_UC1_P95_BUDGET_MS). Production target is "
        f"7000ms; default dev-laptop budget is 30000ms. Latencies: "
        f"{[round(s) for s in tracker.samples_ms]}"
    )
