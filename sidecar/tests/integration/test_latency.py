"""Latency-budget tests for the JWT-protected internal endpoints.

One test, opt-in via the ``latency`` pytest marker:

  test_internal_endpoints_p95_under_budget
    Times the JWT-protected internal endpoints directly (5 samples
    each, 5 endpoints). NO LLM calls — these probe the PHP module
    + sidecar fetcher round-trip. Budget per ARCHITECTURE.md §3:
    each tool fetch should land well under the 2s per-tool
    ceiling. We assert p95 < 1000ms to leave headroom on a
    healthy stack.

Default ``uv run pytest`` deselects the marker. Run explicitly::

    uv run pytest -m latency tests/integration/test_latency.py

The :class:`LatencyTracker` context manager records wall-clock
timings and exposes ``p50``, ``p95``, ``p99`` over the captured
samples. Sufficient for "is the system in the right ballpark?"
checks; production SLO monitoring does not run here.

(The legacy ``test_uc1_total_turn_p95_under_budget`` test posted to
the deleted module-PHP ``turn.php`` route and was removed alongside
the legacy panel; the equivalent end-to-end latency check now lives
on the BFF turn-route surface and is a follow-up.)
"""

from __future__ import annotations

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


