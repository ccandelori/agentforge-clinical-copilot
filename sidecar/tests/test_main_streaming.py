"""End-to-end test for the /turn streaming path (week1-gaps Task #10).

The /turn endpoint must switch on ``settings.streaming_enabled``: when
False (the default until #13's verifier-before-emit gate ships), the
existing :class:`TurnResponse` JSON path stays unchanged. When True,
/turn returns a Server-Sent Events stream of ``data: {...}\\n\\n``
frames followed by a terminal ``data: [DONE]\\n\\n``, with the
accumulated cost emitted on a ``final`` frame instead of an
HTTP header (the body has already started by the time we know cost).

Approach: stub the orchestrator so the test exercises the FastAPI
endpoint surface (response type, media type, frame format, cost
landing) without spinning up a real Anthropic client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agentforge.config import get_settings
from agentforge.gateway.auth_gateway import RequestContext, get_request_context
from agentforge.llm.types import (
    LLMResponse,
    StreamEvent,
    StreamFinal,
    StreamTextDelta,
)
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


def _stub_final(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=1000,
        output_tokens=500,
    )


def _build_streaming_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    streaming_enabled: bool,
    events: list[StreamEvent] | None = None,
    reply: str = "ok",
) -> TestClient:
    """Build a TestClient with a stub orchestrator, toggling
    ``streaming_enabled`` via env var so :class:`Settings` picks it up
    on app construction.
    """
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key-32-bytes-aaaaaaaaaaaaa")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv(
        "STREAMING_ENABLED", "true" if streaming_enabled else "false"
    )
    get_settings.cache_clear()

    captured_events = events

    class _StubOrchestrator:
        async def turn(
            self,
            ctx: RequestContext,
            user_message: str,
            *,
            session_id: str | None = None,
        ) -> str:
            del ctx, user_message, session_id
            # Match the real contract: reset cost var on entry, then
            # populate it. The /turn endpoint reads it after the call.
            _TURN_COST_VAR.set(0.0)
            cost = calculate_cost(_TRACE_MODEL, 1000, 500)
            _TURN_COST_VAR.set(cost)
            return reply

        async def stream_turn(
            self,
            ctx: RequestContext,
            user_message: str,
            *,
            session_id: str | None = None,
        ) -> AsyncIterator[StreamEvent]:
            del ctx, user_message, session_id
            _TURN_COST_VAR.set(0.0)
            cost = calculate_cost(_TRACE_MODEL, 1000, 500)
            _TURN_COST_VAR.set(cost)
            assert captured_events is not None
            for event in captured_events:
                yield event

    app = create_app(redis_client=_build_redis_mock())
    app.dependency_overrides[get_orchestrator] = lambda: _StubOrchestrator()
    app.dependency_overrides[get_request_context] = _ctx

    return TestClient(app)


@pytest.fixture
def monkeypatch_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[pytest.MonkeyPatch]:
    yield monkeypatch
    get_settings.cache_clear()


class TestStreamingFlagFallback:
    def test_streaming_disabled_returns_regular_turnresponse(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        # Default off: existing JSON contract is unchanged. Cost still
        # arrives via the X-Agent-Cost-USD header (not as an SSE frame).
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=False, reply="hello"
        )

        response = client.post("/turn", json={"message": "hi"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"reply": "hello"}
        assert "x-agent-cost-usd" in response.headers


class TestStreamingPath:
    def test_streaming_enabled_returns_event_stream(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        events: list[StreamEvent] = [
            StreamTextDelta(text="Hello, "),
            StreamTextDelta(text="Susan!"),
            StreamFinal(response=_stub_final("Hello, Susan!")),
        ]
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=True, events=events
        )

        response = client.post("/turn", json={"message": "hi"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/event-stream"
        )
        # SSE-buffering suppression headers (#10 sets these so nginx /
        # other reverse proxies don't accidentally hold deltas).
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

    def test_each_delta_arrives_as_data_frame(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        events: list[StreamEvent] = [
            StreamTextDelta(text="Hello, "),
            StreamTextDelta(text="Susan!"),
            StreamFinal(response=_stub_final("Hello, Susan!")),
        ]
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=True, events=events
        )

        response = client.post("/turn", json={"message": "hi"})
        body = response.text

        # SSE frame shape: ``data: {...}\n\n``
        assert 'data: {"text": "Hello, "}\n\n' in body
        assert 'data: {"text": "Susan!"}\n\n' in body

    def test_terminal_done_marker_present(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        events: list[StreamEvent] = [
            StreamTextDelta(text="ok"),
            StreamFinal(response=_stub_final("ok")),
        ]
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=True, events=events
        )

        response = client.post("/turn", json={"message": "hi"})
        body = response.text

        # ``[DONE]`` is the explicit terminator — matches OpenAI /
        # Anthropic streaming so any JS reader built against either
        # provider can consume our wire as-is.
        assert body.rstrip().endswith("data: [DONE]")

    def test_final_frame_carries_stop_reason_and_cost(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        # Cost and stop_reason ride on the ``final`` SSE frame because
        # we can't set HTTP headers after the body has started.
        events: list[StreamEvent] = [
            StreamTextDelta(text="ok"),
            StreamFinal(response=_stub_final("ok")),
        ]
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=True, events=events
        )

        response = client.post("/turn", json={"message": "hi"})
        body = response.text

        # The final frame is recognizable by ``"final": true``.
        # Validate the payload contains stop_reason + a parseable
        # cost float matching the stub's tokens.
        expected_cost = calculate_cost(_TRACE_MODEL, 1000, 500)
        # Cost is rounded to 6 decimals by _sse_stream — assert with
        # tolerance rather than exact string match so a future
        # rounding tweak doesn't churn the test.
        import json as _json
        import re

        match = re.search(
            r"data: (\{.*?\"final\".*?\})\n\n", body
        )
        assert match is not None, f"No final frame in body: {body!r}"
        payload = _json.loads(match.group(1))
        assert payload["final"] is True
        assert payload["stop_reason"] == "end_turn"
        assert payload["cost_usd"] == pytest.approx(expected_cost, rel=1e-6)

    def test_frame_order_is_deltas_then_final_then_done(
        self, monkeypatch_env: pytest.MonkeyPatch
    ) -> None:
        events: list[StreamEvent] = [
            StreamTextDelta(text="A"),
            StreamTextDelta(text="B"),
            StreamFinal(response=_stub_final("AB")),
        ]
        client = _build_streaming_client(
            monkeypatch_env, streaming_enabled=True, events=events
        )

        response = client.post("/turn", json={"message": "hi"})
        body = response.text

        # Find the byte offsets of the three sentinel substrings; they
        # must appear in deltas-final-DONE order.
        a_pos = body.find('"text": "A"')
        b_pos = body.find('"text": "B"')
        final_pos = body.find('"final": true')
        done_pos = body.find("[DONE]")

        assert a_pos < b_pos < final_pos < done_pos, (
            f"Unexpected frame order in body: {body!r}"
        )
