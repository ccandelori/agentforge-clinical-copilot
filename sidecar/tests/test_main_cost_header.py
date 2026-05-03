"""End-to-end test for the X-Agent-Cost-USD response header.

The /turn endpoint must surface accumulated LLM cost so the OpenEMR
PHP module can log it next to the user/pid for the request. This is
the wire-level integration test — it asserts the header flows from the
orchestrator's per-turn ContextVar through ``Response.headers`` and
out to the client. Closes Week 1 Task #14 subtask 14.7/14.8.

Approach: dependency-override the orchestrator with a stub that runs
the same ContextVar populator the real orchestrator's
``_record_llm_call`` does, so we exercise the FastAPI endpoint
without spinning up a real Anthropic client.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.gateway.auth_gateway import RequestContext, get_request_context
from agentforge.main import create_app, get_orchestrator
from agentforge.observability.cost import calculate_cost
from agentforge.orchestrator import _TRACE_MODEL, _TURN_COST_VAR


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


def _build_redis_mock() -> AsyncMock:
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=0)
    redis_mock.keys = AsyncMock(return_value=[])
    redis_mock.smembers = AsyncMock(return_value=set())
    return redis_mock


@pytest.fixture
def cost_recording_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, float]]]:
    """A TestClient that records turn cost via a stub orchestrator.

    The stub mimics the real orchestrator's contract: reset the cost
    ContextVar on entry, then accumulate based on whatever the test
    asks for via ``stash["next_cost_usd"]`` / ``next_input_tokens`` /
    ``next_output_tokens``. The /turn endpoint reads the same var
    via ``get_turn_cost_usd`` and sets the header — same code path
    production runs.
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()

    stash: dict[str, Any] = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "reply": "ok",
    }

    class _StubOrchestrator:
        async def turn(
            self,
            ctx: RequestContext,
            user_message: str,
            *,
            session_id: str | None = None,
        ) -> str:
            del ctx, user_message, session_id
            # Mirror the real orchestrator: reset, then accumulate
            # via the same calculate_cost call. The endpoint reads
            # the var after this returns and sets the header.
            _TURN_COST_VAR.set(0.0)
            cost = calculate_cost(
                _TRACE_MODEL,
                stash["input_tokens"],
                stash["output_tokens"],
            )
            _TURN_COST_VAR.set(_TURN_COST_VAR.get() + cost)
            return str(stash["reply"])

    app = create_app(redis_client=_build_redis_mock())
    app.dependency_overrides[get_orchestrator] = lambda: _StubOrchestrator()
    app.dependency_overrides[get_request_context] = _ctx

    with TestClient(app) as client:
        yield client, stash


def test_turn_response_carries_x_agent_cost_usd_header(
    cost_recording_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, stash = cost_recording_client
    stash["input_tokens"] = 1000
    stash["output_tokens"] = 500

    response = client.post("/turn", json={"message": "hi"})

    assert response.status_code == 200
    assert "x-agent-cost-usd" in response.headers
    expected = calculate_cost(_TRACE_MODEL, 1000, 500)
    assert float(response.headers["x-agent-cost-usd"]) == pytest.approx(
        expected, rel=1e-9
    )


def test_turn_cost_header_resets_per_request(
    cost_recording_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """Two sequential /turn calls produce two independent cost
    headers — proves the ContextVar reset at turn() entry holds
    across requests, not just first-time."""
    client, stash = cost_recording_client

    stash["input_tokens"] = 100
    stash["output_tokens"] = 20
    r1 = client.post("/turn", json={"message": "first"})

    stash["input_tokens"] = 5000
    stash["output_tokens"] = 2500
    r2 = client.post("/turn", json={"message": "second"})

    assert r1.status_code == r2.status_code == 200
    cost1 = float(r1.headers["x-agent-cost-usd"])
    cost2 = float(r2.headers["x-agent-cost-usd"])
    assert cost1 == pytest.approx(calculate_cost(_TRACE_MODEL, 100, 20))
    assert cost2 == pytest.approx(calculate_cost(_TRACE_MODEL, 5000, 2500))
    assert cost2 > cost1, "Second turn should have higher cost than first"


def test_turn_cost_header_format_is_six_decimal_usd(
    cost_recording_client: tuple[TestClient, dict[str, Any]],
) -> None:
    """The header value must parse as a float with six decimal places.
    Anthropic's cheapest call is ~$1e-5; three decimals would round
    everything but the largest charts to zero, defeating the point."""
    client, stash = cost_recording_client
    stash["input_tokens"] = 100
    stash["output_tokens"] = 50

    response = client.post("/turn", json={"message": "hi"})

    raw = response.headers["x-agent-cost-usd"]
    # Format check: must be a string parseable as float, and the
    # decimal part should have exactly six digits.
    assert "." in raw
    decimal_digits = raw.split(".", 1)[1]
    assert len(decimal_digits) == 6, (
        f"X-Agent-Cost-USD must use six decimal places; got {raw!r}"
    )
