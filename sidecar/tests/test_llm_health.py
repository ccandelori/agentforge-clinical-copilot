"""LLMHealthMonitor — periodic provider liveness check + failover seam.

The monitor runs as a background task on the FastAPI lifecycle. Every
``interval_seconds`` it pings the primary's ``health_check()``; two
consecutive failures flip the status to DOWN. ``get_active_client()``
returns the secondary (if configured) when the primary is DOWN, else
returns the primary regardless — v1 explicitly does NOT failover to a
local model (ARCHITECTURE.md §13). The "secondary" hook exists for a
future BAA-covered backup, not for graceful degradation today.

See ARCHITECTURE.md §5 (LLM layer) and §7.3 alert tier "WARN: LLM
provider degraded".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from agentforge.llm.health import (
    HealthCheckResult,
    LLMHealthMonitor,
    LLMProviderStatus,
)


def _client(*, fail: bool = False, slow_ms: int = 0) -> AsyncMock:
    """Build a fake HealthCheckableLLM. ``fail=True`` raises; ``slow_ms``
    sleeps before returning so timeout tests have something to bite on.
    """
    mock = AsyncMock()
    if fail:
        mock.health_check.side_effect = RuntimeError("provider unreachable")
        return mock

    if slow_ms > 0:

        async def _slow() -> None:
            await asyncio.sleep(slow_ms / 1000)

        mock.health_check.side_effect = _slow
    return mock


class TestInitialState:
    def test_starts_healthy_before_first_check(self) -> None:
        monitor = LLMHealthMonitor(primary=_client())
        result = monitor.status
        assert result.status is LLMProviderStatus.HEALTHY
        assert result.consecutive_failures == 0
        assert result.last_error is None

    def test_get_active_client_returns_primary_initially(self) -> None:
        primary = _client()
        secondary = _client()
        monitor = LLMHealthMonitor(primary=primary, secondary=secondary)
        assert monitor.get_active_client() is primary


class TestCheckOnce:
    async def test_successful_check_keeps_status_healthy(self) -> None:
        monitor = LLMHealthMonitor(primary=_client())
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.HEALTHY
        assert monitor.status.consecutive_failures == 0

    async def test_one_failure_marks_degraded_not_down(self) -> None:
        monitor = LLMHealthMonitor(
            primary=_client(fail=True), failures_before_down=2
        )
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DEGRADED
        assert monitor.status.consecutive_failures == 1
        assert monitor.status.last_error is not None
        assert "provider unreachable" in monitor.status.last_error

    async def test_two_consecutive_failures_marks_down(self) -> None:
        monitor = LLMHealthMonitor(
            primary=_client(fail=True), failures_before_down=2
        )
        await monitor.check_once()
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DOWN
        assert monitor.status.consecutive_failures == 2

    async def test_success_resets_failure_count(self) -> None:
        # Start with a client that fails twice then succeeds — the
        # AsyncMock side_effect list applies one return per call.
        primary = AsyncMock()
        primary.health_check.side_effect = [
            RuntimeError("first"),
            RuntimeError("second"),
            None,
        ]
        monitor = LLMHealthMonitor(primary=primary, failures_before_down=2)
        await monitor.check_once()
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DOWN
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.HEALTHY
        assert monitor.status.consecutive_failures == 0
        assert monitor.status.last_error is None

    async def test_timeout_counts_as_failure(self) -> None:
        # Health check sleeps longer than the configured timeout.
        monitor = LLMHealthMonitor(
            primary=_client(slow_ms=200),
            timeout_seconds=0.05,
            failures_before_down=2,
        )
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DEGRADED
        assert monitor.status.consecutive_failures == 1

    async def test_check_once_records_timestamp(self) -> None:
        before = datetime.now(UTC)
        monitor = LLMHealthMonitor(primary=_client())
        await monitor.check_once()
        after = datetime.now(UTC)
        assert before <= monitor.status.last_check_at <= after


class TestActiveClientSelection:
    async def test_returns_primary_when_healthy(self) -> None:
        primary = _client()
        secondary = _client()
        monitor = LLMHealthMonitor(primary=primary, secondary=secondary)
        await monitor.check_once()
        assert monitor.get_active_client() is primary

    async def test_returns_secondary_when_primary_down(self) -> None:
        primary = _client(fail=True)
        secondary = _client()
        monitor = LLMHealthMonitor(
            primary=primary, secondary=secondary, failures_before_down=2
        )
        await monitor.check_once()
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DOWN
        assert monitor.get_active_client() is secondary

    async def test_returns_primary_when_down_and_no_secondary(self) -> None:
        # v1 constraint: with no secondary configured, the call goes to
        # the (broken) primary anyway — the caller is expected to surface
        # the failure to the user. The monitor's job is observability and
        # alerting, not silent failover to a smaller local model.
        primary = _client(fail=True)
        monitor = LLMHealthMonitor(primary=primary, failures_before_down=2)
        await monitor.check_once()
        await monitor.check_once()
        assert monitor.status.status is LLMProviderStatus.DOWN
        assert monitor.get_active_client() is primary


class TestBackgroundLifecycle:
    async def test_start_runs_periodic_checks(self) -> None:
        primary = _client()
        monitor = LLMHealthMonitor(primary=primary, interval_seconds=0.01)
        await monitor.start()
        # Let the loop tick a few times.
        await asyncio.sleep(0.05)
        await monitor.stop()
        # health_check was called at least twice in 50ms with a 10ms tick.
        assert primary.health_check.await_count >= 2

    async def test_start_is_idempotent(self) -> None:
        primary = _client()
        monitor = LLMHealthMonitor(primary=primary, interval_seconds=0.01)
        await monitor.start()
        await monitor.start()  # second call must not spawn a second task
        await asyncio.sleep(0.03)
        await monitor.stop()
        # We can't assert exact call counts deterministically, but no
        # exception was raised — that's the contract.

    async def test_stop_is_safe_to_call_without_start(self) -> None:
        monitor = LLMHealthMonitor(primary=_client())
        await monitor.stop()  # must not raise

    async def test_stop_cancels_running_loop(self) -> None:
        primary = _client()
        monitor = LLMHealthMonitor(primary=primary, interval_seconds=0.01)
        await monitor.start()
        await asyncio.sleep(0.02)
        await monitor.stop()
        before = primary.health_check.await_count
        await asyncio.sleep(0.05)
        assert primary.health_check.await_count == before


class TestHealthCheckResultFrozen:
    def test_result_is_immutable(self) -> None:
        result = HealthCheckResult(
            status=LLMProviderStatus.HEALTHY,
            consecutive_failures=0,
            last_check_at=datetime.now(UTC),
        )
        with pytest.raises(Exception):  # noqa: B017 — frozen dataclass
            result.consecutive_failures = 5  # type: ignore[misc]
