"""Tests for the timeout / retry policy module.

The module owns three responsibilities:
  * TimeoutPolicy / RetryPolicy frozen-dataclass configs (defaults
    match ARCHITECTURE.md S9).
  * Error classification — mapping httpx exceptions to the policy's
    retryable-error vocabulary.
  * retry_with_policy — generic async helper that runs a callable,
    classifies its failure, and retries within the remaining budget.

GracefulDegradation just formats a user-visible degradation notice —
the orchestrator decides where to surface it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentforge.timeouts import (
    GracefulDegradation,
    RetryPolicy,
    TimeoutPolicy,
    classify_http_error,
    retry_with_policy,
)

# ---------- TimeoutPolicy defaults ----------


def test_timeout_policy_defaults_match_architecture_doc() -> None:
    policy = TimeoutPolicy()
    assert policy.per_tool == 2.0
    assert policy.tool_phase == 4.0
    assert policy.total_turn == 7.0
    assert policy.max_steps == 7
    assert policy.synthesis_input_cap == 12000


def test_timeout_policy_is_immutable() -> None:
    policy = TimeoutPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.per_tool = 99.0  # type: ignore[misc]


def test_timeout_policy_is_overridable() -> None:
    policy = TimeoutPolicy(per_tool=5.0, total_turn=15.0)
    assert policy.per_tool == 5.0
    assert policy.total_turn == 15.0
    # Other fields keep defaults
    assert policy.tool_phase == 4.0


# ---------- RetryPolicy defaults ----------


def test_retry_policy_defaults_match_architecture_doc() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 3
    assert policy.per_attempt_timeout == 0.5
    assert policy.backoff_base == 0.1
    assert policy.backoff_factor == 2.0
    assert policy.retryable_errors == ("timeout", "network", "503", "504")


def test_retry_policy_is_immutable() -> None:
    policy = RetryPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.max_attempts = 99  # type: ignore[misc]


# ---------- should_retry ----------


def test_should_retry_returns_true_for_retryable_error_with_budget() -> None:
    policy = RetryPolicy()
    assert policy.should_retry("timeout", remaining_budget=2.0) is True
    assert policy.should_retry("network", remaining_budget=1.0) is True
    assert policy.should_retry("503", remaining_budget=0.7) is True
    assert policy.should_retry("504", remaining_budget=0.6) is True


def test_should_retry_returns_false_for_non_retryable_error() -> None:
    policy = RetryPolicy()
    assert policy.should_retry("400", remaining_budget=10.0) is False
    assert policy.should_retry("401", remaining_budget=10.0) is False
    assert policy.should_retry("404", remaining_budget=10.0) is False
    assert policy.should_retry("permission_denied", remaining_budget=10.0) is False
    assert policy.should_retry("validation", remaining_budget=10.0) is False


def test_should_retry_returns_false_when_remaining_budget_below_threshold() -> None:
    # 600ms budget threshold per the spec — anything less skips retry.
    policy = RetryPolicy()
    assert policy.should_retry("timeout", remaining_budget=0.59) is False
    assert policy.should_retry("503", remaining_budget=0.0) is False
    assert policy.should_retry("network", remaining_budget=-1.0) is False


# ---------- compute_backoff ----------


def test_compute_backoff_uses_exponential_factor() -> None:
    # backoff_base=0.1, backoff_factor=2.0
    # attempt=0 → no prior failures (shouldn't be called); attempt 1 → 0.1
    # attempt 2 → 0.2; attempt 3 → 0.4
    policy = RetryPolicy()
    assert policy.compute_backoff(1) == pytest.approx(0.1)
    assert policy.compute_backoff(2) == pytest.approx(0.2)
    assert policy.compute_backoff(3) == pytest.approx(0.4)


def test_compute_backoff_respects_overridden_constants() -> None:
    policy = RetryPolicy(backoff_base=0.5, backoff_factor=3.0)
    assert policy.compute_backoff(1) == pytest.approx(0.5)
    assert policy.compute_backoff(2) == pytest.approx(1.5)


# ---------- classify_http_error ----------


def test_classify_http_error_maps_timeout_exception() -> None:
    exc = httpx.ReadTimeout("read timeout")
    assert classify_http_error(exc) == "timeout"


def test_classify_http_error_maps_connect_error_to_network() -> None:
    exc = httpx.ConnectError("connection refused")
    assert classify_http_error(exc) == "network"


def test_classify_http_error_maps_503_status_error() -> None:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
    assert classify_http_error(exc) == "503"


def test_classify_http_error_maps_504_status_error() -> None:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(504, request=request)
    exc = httpx.HTTPStatusError("Gateway Timeout", request=request, response=response)
    assert classify_http_error(exc) == "504"


def test_classify_http_error_returns_none_for_4xx() -> None:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("Unauthorized", request=request, response=response)
    assert classify_http_error(exc) is None


def test_classify_http_error_returns_none_for_5xx_other_than_503_504() -> None:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("Internal Server Error", request=request, response=response)
    # 500 is non-retryable per the spec — a 500 means the upstream
    # request is malformed or the server is hitting a real bug; retrying
    # won't help. Only 503/504 (overloaded / gateway timeout) are
    # transient.
    assert classify_http_error(exc) is None


def test_classify_http_error_returns_none_for_unrelated_exceptions() -> None:
    assert classify_http_error(ValueError("nope")) is None
    assert classify_http_error(RuntimeError("also nope")) is None


# ---------- retry_with_policy ----------


async def test_retry_with_policy_returns_result_on_first_success() -> None:
    call = AsyncMock(return_value="ok")
    policy = RetryPolicy()

    result = await retry_with_policy(call, policy=policy, total_budget=10.0)

    assert result == "ok"
    assert call.await_count == 1


async def test_retry_with_policy_retries_on_transient_error_and_succeeds() -> None:
    request = httpx.Request("GET", "https://example.test")
    transient = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    call = AsyncMock(side_effect=[transient, "ok"])
    policy = RetryPolicy()
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    result = await retry_with_policy(
        call, policy=policy, total_budget=10.0, sleep=_sleep,
    )

    assert result == "ok"
    assert call.await_count == 2
    # First retry waits backoff_base = 0.1
    assert sleeps == [pytest.approx(0.1)]


async def test_retry_with_policy_does_not_retry_non_transient_error() -> None:
    request = httpx.Request("GET", "https://example.test")
    fatal = httpx.HTTPStatusError(
        "401",
        request=request,
        response=httpx.Response(401, request=request),
    )
    call = AsyncMock(side_effect=fatal)
    policy = RetryPolicy()

    with pytest.raises(httpx.HTTPStatusError):
        await retry_with_policy(call, policy=policy, total_budget=10.0)
    assert call.await_count == 1


async def test_retry_with_policy_gives_up_after_max_attempts() -> None:
    request = httpx.Request("GET", "https://example.test")
    transient = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    call = AsyncMock(side_effect=transient)
    policy = RetryPolicy()
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_with_policy(
            call, policy=policy, total_budget=10.0, sleep=_sleep,
        )

    # max_attempts=3 → 1 initial + 2 retries
    assert call.await_count == 3
    # Two backoffs between the three attempts: 0.1, 0.2
    assert sleeps == [pytest.approx(0.1), pytest.approx(0.2)]


async def test_retry_with_policy_skips_retry_when_budget_exhausted() -> None:
    # Budget so tight there's no room to retry — first failure should
    # propagate without a second attempt.
    request = httpx.Request("GET", "https://example.test")
    transient = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    call = AsyncMock(side_effect=transient)
    policy = RetryPolicy()

    with pytest.raises(httpx.HTTPStatusError):
        await retry_with_policy(call, policy=policy, total_budget=0.3)

    # Single attempt — no retry because remaining budget < 0.6
    assert call.await_count == 1


async def test_retry_with_policy_decrements_budget_using_monotonic_clock() -> None:
    # Each attempt + sleep eats budget. We mock the clock so we can
    # verify the helper actually computes remaining and skips when it
    # falls below threshold.
    request = httpx.Request("GET", "https://example.test")
    transient = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request),
    )
    call = AsyncMock(side_effect=transient)
    policy = RetryPolicy()

    # Each call to monotonic() returns a value 0.5s later than the prior.
    times = iter([0.0, 0.5, 1.0, 1.5, 2.0])
    monotonic = MagicMock(side_effect=lambda: next(times))

    async def _sleep(_: float) -> None:
        return None

    with pytest.raises(httpx.HTTPStatusError):
        await retry_with_policy(
            call,
            policy=policy,
            total_budget=1.0,
            sleep=_sleep,
            monotonic=monotonic,
        )

    # Budget=1.0; after first attempt at t=0.5, remaining=0.5 < 0.6 → no retry
    assert call.await_count == 1


# ---------- GracefulDegradation ----------


def test_graceful_degradation_formats_single_tool() -> None:
    notice = GracefulDegradation.format_degradation_notice(["get_recent_labs"])
    assert "did not respond" in notice
    assert "get_recent_labs" in notice
    assert "may be incomplete" in notice


def test_graceful_degradation_formats_multiple_tools_with_comma_separator() -> None:
    notice = GracefulDegradation.format_degradation_notice(
        ["get_recent_labs", "get_vitals_trend"]
    )
    assert "get_recent_labs" in notice
    assert "get_vitals_trend" in notice


def test_graceful_degradation_returns_empty_for_no_timeouts() -> None:
    # Nothing timed out → the helper returns the empty string so the
    # caller can append it unconditionally without producing dangling
    # parentheses or trailing whitespace.
    notice = GracefulDegradation.format_degradation_notice([])
    assert notice == ""
