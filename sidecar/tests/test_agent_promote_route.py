"""Integration tests for the BFF ``POST /api/agent/promote/intake`` route.

End-to-end wiring from cookie → session → resolved identity → minted
internal JWT → AuthGateway-validated RequestContext → IntakePromoteWriter.

The IntakePromoteWriter is replaced by a stub that captures its inputs
so we can assert the bridge produces the right ``jwt`` and ``body``
for downstream OpenEMR. The OpenEMR ``/me`` and ``/patient_pid``
endpoints are faked via :class:`httpx.MockTransport` on the BFF's
fetchers, mirroring the upload-route test setup.
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
from agentforge.dashboard_auth.promote_route import make_agent_promote_router
from agentforge.gateway.auth_gateway import AuthGateway
from agentforge.tools.intake_promote import IntakePromoteError


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


class _CapturingPromoteWriter:
    """Captures the body the BFF would forward to the PHP endpoint."""

    def __init__(self, *, response: dict[str, Any] | None = None) -> None:
        self.captured: dict[str, Any] = {}
        self.raise_with: IntakePromoteError | None = None
        # Default receipt mirrors the PHP controller's serialised
        # PromotionResult shape: a list of inserted handles + count.
        self._response = response or {
            "promoted": [
                {"kind": "allergy", "lists_id": 4001, "title": "Penicillin"},
                {
                    "kind": "medical_problem",
                    "lists_id": 4002,
                    "title": "Type 2 diabetes",
                },
            ],
            "count": 2,
        }

    async def promote(
        self,
        *,
        jwt: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        self.captured = {"jwt": jwt, "body": body}
        if self.raise_with is not None:
            raise self.raise_with
        return self._response


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
    promote_writer: _CapturingPromoteWriter | None = None,
) -> tuple[FastAPI, _CapturingPromoteWriter, SessionStore]:
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

    writer = promote_writer or _CapturingPromoteWriter()

    router = make_agent_promote_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        promote_writer=writer,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)
    return app, writer, session_store


def _well_formed_body(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "patient_uuid": "patient-resource-uuid",
        "items": [
            {"kind": "allergy", "title": "Penicillin", "details": "rash"},
            {"kind": "medical_problem", "title": "Type 2 diabetes"},
        ],
        "questionnaire_response_id": "qr-uuid-1",
        "document_id": "777",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        resp = client.post("/api/agent/promote/intake", json=_well_formed_body())
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_with_no_fhir_user_returns_401() -> None:
    app, _, store = _build_app()
    sid = await _seed_session(store, fhir_user="")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post("/api/agent/promote/intake", json=_well_formed_body())
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_happy_path_round_trips_through_writer() -> None:
    app, writer, store = _build_app(
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
            "/api/agent/promote/intake",
            json=_well_formed_body(),
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 2
    assert len(payload["promoted"]) == 2

    # The body the writer received carries the resolved integer
    # patient_id (NOT the patient_uuid) plus the items verbatim and
    # the optional audit/lineage hints.
    body = writer.captured["body"]
    assert body["patient_id"] == 42
    assert body["questionnaire_response_id"] == "qr-uuid-1"
    assert body["document_id"] == 777  # parsed string-as-int
    assert len(body["items"]) == 2
    assert body["items"][0] == {
        "kind": "allergy",
        "title": "Penicillin",
        "details": "rash",
    }
    # No-details item drops the key rather than emitting null — the
    # PHP side treats missing details as None.
    assert body["items"][1] == {
        "kind": "medical_problem",
        "title": "Type 2 diabetes",
    }

    # The JWT is the one minted by InternalJwtMinter for this turn —
    # we don't try to round-trip it, just confirm it's a non-empty
    # bearer-shaped string. The PHP side validates signature + claims.
    jwt = writer.captured["jwt"]
    assert isinstance(jwt, str) and len(jwt) > 0


@pytest.mark.asyncio
async def test_rejects_empty_items_list() -> None:
    """Pydantic min_length=1 — empty items is a 422."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(items=[]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_unknown_item_kind() -> None:
    """A 'lab_result' kind is not in the allowed-set — 422 from Pydantic."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(
                items=[{"kind": "lab_result", "title": "Glucose"}],
            ),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_empty_title() -> None:
    """Pydantic min_length=1 — an empty-title item is 422."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(
                items=[{"kind": "allergy", "title": ""}],
            ),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_oversize_batch() -> None:
    """101 items exceeds the BFF's 100-item cap → 422."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    big_items = [
        {"kind": "allergy", "title": f"Allergy {i}"} for i in range(101)
    ]
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(items=big_items),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_garbage_document_id_returns_422() -> None:
    """A non-integer document_id at the BFF boundary fails before forwarding."""
    app, writer, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(document_id="not-an-int"),
        )
    assert resp.status_code == 422
    # Writer was never called — the BFF rejected before forwarding.
    assert writer.captured == {}


@pytest.mark.asyncio
async def test_writer_transport_failure_maps_to_503() -> None:
    """status_code == 0 from the writer (transport failure) → 503."""
    writer = _CapturingPromoteWriter()
    writer.raise_with = IntakePromoteError(
        status_code=0,
        message="boom",
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        promote_writer=writer,
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(),
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_writer_upstream_error_maps_to_502_with_status() -> None:
    """A 4xx/5xx upstream surfaces as 502 with the upstream status."""
    writer = _CapturingPromoteWriter()
    writer.raise_with = IntakePromoteError(
        status_code=403,
        message="upstream forbade",
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        promote_writer=writer,
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(),
        )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["sidecar_upstream_status"] == 403


@pytest.mark.asyncio
async def test_me_failure_maps_to_502() -> None:
    """A failing /me lookup surfaces as 502 from the BFF."""
    app, _, store = _build_app(
        me_response=500,
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(),
        )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["stage"] == "me"


@pytest.mark.asyncio
async def test_patient_pid_failure_maps_to_502() -> None:
    """A failing patient_pid lookup surfaces as 502 from the BFF."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response=404,
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/promote/intake",
            json=_well_formed_body(),
        )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["stage"] == "patient_pid"


@pytest.mark.asyncio
async def test_optional_audit_hints_are_omitted_when_absent() -> None:
    """questionnaire_response_id + document_id default to absent."""
    app, writer, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    body_no_hints = {
        "patient_uuid": "patient-resource-uuid",
        "items": [{"kind": "allergy", "title": "Penicillin"}],
    }
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post("/api/agent/promote/intake", json=body_no_hints)
    assert resp.status_code == 200

    forwarded = writer.captured["body"]
    assert "questionnaire_response_id" not in forwarded
    assert "document_id" not in forwarded


@pytest.mark.asyncio
async def test_extra_keys_rejected_as_422() -> None:
    """Pydantic extra='forbid' on the request body."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(store, fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x")

    body_with_extra = _well_formed_body(unexpected_key="leak")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post("/api/agent/promote/intake", json=body_with_extra)
    assert resp.status_code == 422
