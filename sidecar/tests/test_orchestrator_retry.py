"""Orchestrator retry + graceful-degradation wiring (Task 41).

The orchestrator wraps each fetcher dispatch with
:func:`agentforge.timeouts.retry_with_policy`, retrying on transient
errors (503/504/timeout/network) and giving up cleanly on the rest.
When a tool exhausts its retries due to a timeout, its name lands in
the per-turn ``timed_out_tools`` list and the final reply gets a
short degradation notice appended so the user knows the response is
incomplete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse, ToolCall
from agentforge.orchestrator import Orchestrator
from agentforge.timeouts import RetryPolicy, TimeoutPolicy
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult


def _ctx() -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=7,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _problems_result() -> ProblemsResult:
    return ProblemsResult(
        metadata=ToolResultMetadata(
            tool_name="get_active_problems",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=60,
            source="openemr.problems",
        ),
        payload=ProblemsPayload(
            problems=(ProblemItem(id=1, title="Hypertension"),),
        ),
    )


def _503() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    return httpx.HTTPStatusError(
        "503", request=request, response=httpx.Response(503, request=request)
    )


def _401() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    return httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )


async def _no_sleep(_: float) -> None:
    return None


def _llm_two_step(tool_input: dict[str, object]) -> AsyncMock:
    """LLM that calls one tool then emits final text."""
    tool_use = LLMResponse(
        text="",
        tool_calls=[ToolCall(id="c1", name="get_active_problems", input=tool_input)],
        stop_reason="tool_use",
        input_tokens=10,
        output_tokens=5,
    )
    final = LLMResponse(
        text="Patient has hypertension.",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=20,
        output_tokens=8,
    )
    mock = AsyncMock()
    mock.complete.side_effect = [tool_use, final]
    return mock


def _build(*, llm: AsyncMock, problems: AsyncMock) -> Orchestrator:
    # Tight retry policy so backoff doesn't slow tests; sleep is also
    # mocked. Total budget large enough for three attempts to fit.
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=problems,
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        timeout_policy=TimeoutPolicy(per_tool=10.0),
        retry_policy=RetryPolicy(),
        sleep=_no_sleep,
    )


class TestRetryOnTransientError:
    async def test_503_retried_once_then_succeeds(self) -> None:
        problems_fetcher = AsyncMock()
        problems_fetcher.fetch.side_effect = [_503(), _problems_result()]

        llm = _llm_two_step({})
        orch = _build(llm=llm, problems=problems_fetcher)

        reply = await orch.turn(_ctx(), "What problems?")

        assert reply == "Patient has hypertension."
        assert problems_fetcher.fetch.await_count == 2


class TestNoRetryOnFatalError:
    async def test_401_not_retried(self) -> None:
        problems_fetcher = AsyncMock()
        problems_fetcher.fetch.side_effect = _401()

        llm = _llm_two_step({})
        orch = _build(llm=llm, problems=problems_fetcher)

        await orch.turn(_ctx(), "What problems?")

        # Single attempt — 401 is not retryable.
        assert problems_fetcher.fetch.await_count == 1


class TestPersistentTimeoutAddsDegradationNotice:
    async def test_repeated_timeouts_surface_notice_in_reply(self) -> None:
        problems_fetcher = AsyncMock()
        problems_fetcher.fetch.side_effect = [
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
        ]

        llm = _llm_two_step({})
        orch = _build(llm=llm, problems=problems_fetcher)

        reply = await orch.turn(_ctx(), "What problems?")

        # Three attempts (initial + 2 retries).
        assert problems_fetcher.fetch.await_count == 3
        # Degradation notice appended to the model's final reply.
        assert "did not respond in time" in reply
        assert "get_active_problems" in reply

    async def test_no_notice_when_all_tools_succeed(self) -> None:
        problems_fetcher = AsyncMock()
        problems_fetcher.fetch.return_value = _problems_result()

        llm = _llm_two_step({})
        orch = _build(llm=llm, problems=problems_fetcher)

        reply = await orch.turn(_ctx(), "What problems?")

        assert "did not respond in time" not in reply
