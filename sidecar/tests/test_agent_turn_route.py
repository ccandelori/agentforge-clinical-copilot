"""Integration tests for the BFF ``POST /api/agent/turn`` route.

End-to-end wiring from cookie → session → resolved identity → minted
internal JWT → AuthGateway-validated RequestContext → orchestrator.

The Orchestrator is replaced by a stub that captures its inputs so we
can assert the bridge produces the right ``ctx`` for downstream tools.
The OpenEMR ``/me`` endpoint is faked via :class:`httpx.MockTransport`
on the OpenEMRMeFetcher.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.config import Settings
from agentforge.dashboard_auth import SessionStore
from agentforge.dashboard_auth.internal_jwt import InternalJwtMinter
from agentforge.dashboard_auth.openemr_me import OpenEMRMeFetcher
from agentforge.dashboard_auth.openemr_patient_pid import OpenEMRPatientPidFetcher
from agentforge.dashboard_auth.turn_route import make_agent_turn_router
from agentforge.gateway.auth_gateway import AuthGateway, RequestContext


SECRET = "a-very-long-test-secret-that-is-at-least-32b"
SESSION_COOKIE = "agentforge_session"
OPENEMR_BASE = "https://openemr.example"


class _Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


def _make_redis_mock() -> AsyncMock:
    storage: dict[str, str] = {}
    redis_mock = AsyncMock()

    async def get(key: str) -> str | None:
        return storage.get(key)

    async def setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    async def delete(*keys: str) -> int:
        n = 0
        for k in keys:
            if k in storage:
                del storage[k]
                n += 1
        return n

    redis_mock.get.side_effect = get
    redis_mock.setex.side_effect = setex
    redis_mock.delete.side_effect = delete
    return redis_mock


class _CapturingOrchestrator:
    """Minimal Orchestrator stand-in: captures ``turn`` inputs."""

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.captured: dict[str, Any] = {}

    async def turn(
        self,
        ctx: RequestContext,
        user_message: str,
        *,
        session_id: str | None = None,
        **_: Any,
    ) -> str:
        self.captured = {
            "ctx": ctx,
            "user_message": user_message,
            "session_id": session_id,
        }
        return self.reply


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="x",
        jwt_secret=SECRET,
        dashboard_session_cookie_name=SESSION_COOKIE,
        redis_url="redis://localhost:6379/0",
        hmac_key="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1zZWNyZXQtdGVzdGluZw==",
    )


async def _seed_session(
    store: SessionStore,
    *,
    fhir_user: str,
) -> str:
    session = await store.create_session(
        sub="oauth-sub-uuid",
        access_token="access-tok",
        expires_at=9_999_999_999.0,
        fhir_user=fhir_user,
    )
    return session.session_id


def _build_app(
    *,
    me_response: dict[str, Any] | int = 200,
    pid_response: dict[str, Any] | int = 200,
    orchestrator: _CapturingOrchestrator | None = None,
) -> tuple[FastAPI, _CapturingOrchestrator, SessionStore]:
    settings = _settings()
    redis = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    def me_handler(request: httpx.Request) -> httpx.Response:
        if isinstance(me_response, int):
            return httpx.Response(me_response, json={"error": "synthetic"})
        return httpx.Response(200, json=me_response)

    def pid_handler(request: httpx.Request) -> httpx.Response:
        if isinstance(pid_response, int):
            return httpx.Response(pid_response, json={"error": "synthetic"})
        return httpx.Response(200, json=pid_response)

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=SECRET, clock=_Clock())
    gateway = AuthGateway(jwt_secret=SECRET, redis_client=None)

    orch = orchestrator or _CapturingOrchestrator()

    router = make_agent_turn_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        orchestrator=orch,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)
    return app, orch, session_store


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p-uuid"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_with_no_fhir_user_returns_401() -> None:
    app, _, store = _build_app()
    sid = await _seed_session(store, fhir_user="")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p-uuid"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_happy_path_round_trips_through_gateway_to_orchestrator() -> None:
    app, orch, store = _build_app(
        me_response={
            "user_id": 17,
            "username": "admin",
            "role": "Administrators",
        },
        pid_response={"pid": 42},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/abc-uuid-123",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "what's the patient's allergies?",
                "patient_uuid": "patient-resource-uuid",
                "session_id": "convo-1",
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"reply": "ok"}

    # Orchestrator received the right ctx — user_id from /me, pid from
    # /patient_pid — both routed through the real AuthGateway.
    ctx: RequestContext = orch.captured["ctx"]
    assert ctx.user_id == 17
    assert ctx.patient_id == 42
    assert ctx.username == "admin"
    assert ctx.role == "Administrators"
    assert orch.captured["user_message"] == "what's the patient's allergies?"
    assert orch.captured["session_id"] == "convo-1"


@pytest.mark.asyncio
async def test_uuid_extraction_handles_bare_practitioner_resource() -> None:
    """``fhirUser`` can also arrive as ``Practitioner/<uuid>`` without a
    full URI prefix; the bridge must accept either shape."""
    app, orch, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 99},
    )
    sid = await _seed_session(store, fhir_user="Practitioner/just-a-uuid")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p"},
        )

    assert resp.status_code == 200
    assert orch.captured["ctx"].user_id == 1


@pytest.mark.asyncio
async def test_me_endpoint_404_returns_502() -> None:
    """If OpenEMR has no row for the session's UUID, the bridge surfaces
    a 502 (bad gateway)."""
    app, _, store = _build_app(me_response=404, pid_response={"pid": 1})
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/missing",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "p"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_patient_pid_endpoint_404_returns_502() -> None:
    """Likewise for an unknown patient UUID — the BFF stays healthy
    but the request can't proceed without the resolved pid."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response=404,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/u",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": "missing-patient"},
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_request_with_empty_patient_uuid_returns_422() -> None:
    """Pydantic enforces ``min_length=1`` on patient_uuid."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x",
    )
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/turn",
            json={"message": "hi", "patient_uuid": ""},
        )
    # FastAPI/Pydantic surfaces validation errors as 422.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_identity_and_pid_lookups_are_cached() -> None:
    """Second turn on the same session + patient should not re-hit
    /me OR /patient_pid — caching keeps the round-trips off the hot
    path."""
    settings = _settings()
    redis = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    me_invocations = {"count": 0}
    pid_invocations = {"count": 0}

    def me_handler(request: httpx.Request) -> httpx.Response:
        me_invocations["count"] += 1
        return httpx.Response(
            200,
            json={"user_id": 1, "username": "u", "role": None},
        )

    def pid_handler(request: httpx.Request) -> httpx.Response:
        pid_invocations["count"] += 1
        return httpx.Response(200, json={"pid": 99})

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=OPENEMR_BASE,
        jwt_secret=SECRET,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=SECRET, clock=_Clock())
    gateway = AuthGateway(jwt_secret=SECRET, redis_client=None)
    orch = _CapturingOrchestrator()

    router = make_agent_turn_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        orchestrator=orch,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)

    sid = await _seed_session(
        session_store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/cached-uuid",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        for _ in range(3):
            resp = client.post(
                "/api/agent/turn",
                json={"message": "hi", "patient_uuid": "p-uuid"},
            )
            assert resp.status_code == 200

    # 3 turns → 1 /me call AND 1 /patient_pid call.
    assert me_invocations["count"] == 1
    assert pid_invocations["count"] == 1
