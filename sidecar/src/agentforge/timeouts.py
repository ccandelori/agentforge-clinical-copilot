"""Timeout and retry policy for agent tool calls.

Three concerns live here:

  * :class:`TimeoutPolicy` — config dataclass with the four budget
    levels from ARCHITECTURE.md S9 (per-tool, tool-phase, total-turn,
    max-steps, synthesis-input-cap).
  * :class:`RetryPolicy` — config dataclass + ``should_retry`` /
    ``compute_backoff`` methods. Retries only on the four transient
    error classes (``timeout``, ``network``, ``503``, ``504``) and
    only when the remaining budget is large enough for the retry to
    plausibly succeed.
  * :func:`retry_with_policy` — the generic async wrapper that applies
    a :class:`RetryPolicy` to any awaitable. It classifies failures
    via :func:`classify_http_error`, sleeps with exponential backoff,
    and decrements the running budget against an injectable monotonic
    clock so tests are deterministic.

:class:`GracefulDegradation` formats the user-visible notice the
orchestrator can append to a partial response. It's a tiny helper
rather than a stateful class because the wiring decision (which tools
timed out, where to surface the message) belongs to the caller, not
the policy.
"""

from __future__ import annotations

import asyncio
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

# Below this much remaining budget the retry helper gives up. Chosen
# to leave room for the next attempt to complete plus a small margin.
_MIN_RETRY_BUDGET_SECONDS = 0.6


@dataclass(frozen=True)
class TimeoutPolicy:
    """Per-turn budget hierarchy. Wider scopes contain narrower ones."""

    per_tool: float = 2.0
    tool_phase: float = 4.0
    total_turn: float = 7.0
    max_steps: int = 7
    synthesis_input_cap: int = 12000


@dataclass(frozen=True)
class RetryPolicy:
    """Retry-on-transient-error policy with exponential backoff."""

    max_attempts: int = 3  # initial attempt + (max_attempts - 1) retries
    per_attempt_timeout: float = 0.5
    backoff_base: float = 0.1
    backoff_factor: float = 2.0
    retryable_errors: tuple[str, ...] = field(
        default=("timeout", "network", "503", "504")
    )

    def should_retry(self, error_type: str, remaining_budget: float) -> bool:
        """Whether to retry given the error class and remaining budget.

        Two conditions must hold:
          * The error class is in the policy's retryable set.
          * The remaining budget is at least
            :data:`_MIN_RETRY_BUDGET_SECONDS` seconds — too little
            headroom and the retry will time out before producing a
            result, which is just delay, not recovery.
        """
        if error_type not in self.retryable_errors:
            return False
        return remaining_budget >= _MIN_RETRY_BUDGET_SECONDS

    def compute_backoff(self, attempt: int) -> float:
        """Backoff before the ``attempt``-th retry (1-indexed).

        attempt=1 → backoff_base; attempt=2 → backoff_base * factor;
        attempt=3 → backoff_base * factor^2; etc.
        """
        return self.backoff_base * (self.backoff_factor ** (attempt - 1))


def classify_http_error(exc: BaseException) -> str | None:
    """Map an exception to the policy's retryable-error vocabulary.

    Returns the matching label (``timeout`` / ``network`` / ``503`` /
    ``504``) or ``None`` for anything not retryable. ``None`` is
    explicitly the "fatal" answer — non-retryable, the caller should
    re-raise.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        # NetworkError covers ConnectError, ReadError, etc. — anything
        # below the HTTP layer that's not a timeout.
        return "network"
    if isinstance(exc, httpx.HTTPStatusError):
        # Only 503 and 504 are retryable. 500 means the upstream is
        # genuinely broken; retrying delays a useful error message.
        # 5xx outside that range likewise — be specific.
        if exc.response.status_code == 503:
            return "503"
        if exc.response.status_code == 504:
            return "504"
        return None
    return None


async def retry_with_policy[T](
    call: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    total_budget: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = _time.monotonic,
) -> T:
    """Run ``call`` with retry-on-transient-error semantics.

    The first failure is classified via :func:`classify_http_error`;
    a non-retryable result re-raises immediately. A retryable result
    consults :meth:`RetryPolicy.should_retry` against the remaining
    budget, sleeps the computed backoff, and tries again. Up to
    ``policy.max_attempts`` total attempts are made.

    ``sleep`` and ``monotonic`` are injectable so tests can run
    deterministically without real wall-clock waits.
    """
    start = monotonic()
    last_exc: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await call()
        except BaseException as exc:
            last_exc = exc
            error_type = classify_http_error(exc)
            if error_type is None:
                # Non-retryable: re-raise immediately so the caller can
                # surface the underlying failure to the user/agent.
                raise

            if attempt == policy.max_attempts:
                # Out of attempts — re-raise the most recent failure.
                raise

            elapsed = monotonic() - start
            remaining = total_budget - elapsed
            if not policy.should_retry(error_type, remaining):
                raise

            backoff = policy.compute_backoff(attempt)
            await sleep(backoff)

    # Loop exits only via return / raise above; this is unreachable but
    # keeps mypy happy.
    assert last_exc is not None
    raise last_exc


class GracefulDegradation:
    """Format a user-visible notice when one or more tools timed out."""

    @staticmethod
    def format_degradation_notice(timed_out_tools: list[str]) -> str:
        """Return the notice text or ``""`` when nothing timed out.

        Empty-string return lets the orchestrator append the result
        unconditionally without producing a dangling "(/)" or trailing
        whitespace when no tool failed.
        """
        if not timed_out_tools:
            return ""
        joined = ", ".join(timed_out_tools)
        return (
            f"Note: Some data sources did not respond in time "
            f"({joined}). Response may be incomplete."
        )
