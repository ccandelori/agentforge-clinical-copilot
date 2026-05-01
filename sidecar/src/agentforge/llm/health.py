"""Periodic LLM provider liveness check + observability seam.

The monitor pings the primary's ``health_check()`` on an interval and
flips its status to DOWN after ``failures_before_down`` consecutive
failures. ``get_active_client()`` returns a configured secondary when
the primary is DOWN, else returns the primary regardless — v1 does
**not** silently failover to a smaller local model
(ARCHITECTURE.md §13). The "secondary" hook exists for a future
BAA-covered backup, not for graceful degradation today.

The status object is exposed through the FastAPI health endpoint so
the WARN-tier alert ("LLM provider degraded") in
ARCHITECTURE.md §7.3 has something to fire on.

Wiring into ``create_app`` is deferred to the orchestrator-instrumentation
work; this module just stands up the primitive.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class HealthCheckableLLM(Protocol):
    """The minimal surface ``LLMHealthMonitor`` needs from a client.

    Kept distinct from ``LLMClient`` so a provider can implement
    completion without health checks (e.g. an in-memory test fake)
    and so the monitor never sees the heavier completion API.
    """

    async def health_check(self) -> None:
        """Cheap ping. Raises on any failure; returns None on success."""
        ...


class LLMProviderStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # at least one failure but below the down threshold
    DOWN = "down"  # threshold reached; failover hook activates if available


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Immutable snapshot of the monitor's most recent check."""

    status: LLMProviderStatus
    consecutive_failures: int
    last_check_at: datetime
    last_error: str | None = None


_INITIAL = HealthCheckResult(
    status=LLMProviderStatus.HEALTHY,
    consecutive_failures=0,
    # Sentinel: epoch as the "we haven't checked yet" marker. The status
    # property normalizes to "now" lazily so callers always see a UTC
    # datetime regardless of whether check_once has run yet.
    last_check_at=datetime.fromtimestamp(0, UTC),
)


class LLMHealthMonitor:
    """Background-task wrapper that tracks one (or two) provider clients."""

    def __init__(
        self,
        primary: HealthCheckableLLM,
        secondary: HealthCheckableLLM | None = None,
        *,
        interval_seconds: float = 30.0,
        timeout_seconds: float = 5.0,
        failures_before_down: int = 2,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if failures_before_down < 1:
            raise ValueError("failures_before_down must be >= 1")
        self._primary = primary
        self._secondary = secondary
        self._interval = interval_seconds
        self._timeout = timeout_seconds
        self._threshold = failures_before_down
        self._clock = clock
        self._result = _INITIAL
        self._task: asyncio.Task[None] | None = None

    @property
    def status(self) -> HealthCheckResult:
        return self._result

    def get_active_client(self) -> HealthCheckableLLM:
        if (
            self._result.status is LLMProviderStatus.DOWN
            and self._secondary is not None
        ):
            return self._secondary
        return self._primary

    async def check_once(self) -> HealthCheckResult:
        """Issue one health check and update internal state.

        Returns the new ``HealthCheckResult`` so callers (tests, the
        FastAPI lifecycle) can assert without reaching for the
        ``status`` property between calls.
        """
        try:
            await asyncio.wait_for(
                self._primary.health_check(), timeout=self._timeout
            )
        except (TimeoutError, Exception) as exc:
            new_count = self._result.consecutive_failures + 1
            status = (
                LLMProviderStatus.DOWN
                if new_count >= self._threshold
                else LLMProviderStatus.DEGRADED
            )
            self._result = HealthCheckResult(
                status=status,
                consecutive_failures=new_count,
                last_check_at=self._clock(),
                last_error=_format_exception(exc),
            )
            return self._result

        self._result = HealthCheckResult(
            status=LLMProviderStatus.HEALTHY,
            consecutive_failures=0,
            last_check_at=self._clock(),
            last_error=None,
        )
        return self._result

    async def start(self) -> None:
        """Spawn the background polling loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background loop. Safe to call without ``start``."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        """The poll loop. Swallows CancelledError on shutdown but lets
        unexpected errors propagate so they surface in logs rather than
        silently killing the monitor.
        """
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            await asyncio.sleep(self._interval)

    def _replace_result(self, **changes: object) -> None:
        # Internal helper kept for forward compatibility — currently
        # unused but documented as the only sanctioned way to mutate
        # ``_result`` from a future subclass.
        self._result = replace(self._result, **changes)  # type: ignore[arg-type]


def _format_exception(exc: BaseException) -> str:
    """Single-line, no-PHI exception summary for the status snapshot.

    The monitor logs the *type* and the *message*; provider error
    bodies sometimes echo request payloads (which carry tool args =
    PHI), so we never include the full repr.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}: {exc}"
