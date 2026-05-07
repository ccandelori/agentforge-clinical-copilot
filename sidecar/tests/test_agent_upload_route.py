"""Integration tests for the BFF ``POST /api/agent/upload`` route.

End-to-end wiring from cookie → session → resolved identity → minted
internal JWT → AuthGateway-validated RequestContext → DocumentUploadWriter.

The DocumentUploadWriter is replaced by a stub that captures its
inputs so we can assert the bridge produces the right ``jwt`` and
``patient_uuid`` for downstream OpenEMR. The OpenEMR ``/me`` and
``/patient_pid`` endpoints are faked via :class:`httpx.MockTransport`
on the BFF's fetchers, mirroring the turn-route test setup.
"""

from __future__ import annotations

import datetime as dt
import io
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
from agentforge.dashboard_auth.upload_route import make_agent_upload_router
from agentforge.gateway.auth_gateway import AuthGateway
from agentforge.tools.document_upload import DocumentUploadError


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


class _CapturingUploadWriter:
    """Minimal DocumentUploadWriter stand-in: captures `upload` inputs."""

    def __init__(self, *, document_id: int = 555) -> None:
        self.document_id = document_id
        self.captured: dict[str, Any] = {}
        self.raise_with: DocumentUploadError | None = None

    async def upload(
        self,
        *,
        jwt: str,
        patient_uuid: str,
        filename: str,
        content: bytes,
        mimetype: str,
        doc_type: str,
        encounter_id: int | None = None,
    ) -> int:
        self.captured = {
            "jwt": jwt,
            "patient_uuid": patient_uuid,
            "filename": filename,
            "content": content,
            "mimetype": mimetype,
            "doc_type": doc_type,
            "encounter_id": encounter_id,
        }
        if self.raise_with is not None:
            raise self.raise_with
        return self.document_id


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
    upload_writer: _CapturingUploadWriter | None = None,
) -> tuple[FastAPI, _CapturingUploadWriter, SessionStore]:
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

    writer = upload_writer or _CapturingUploadWriter()

    router = make_agent_upload_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        document_upload_writer=writer,  # type: ignore[arg-type]
    )

    app = FastAPI()
    app.include_router(router)
    return app, writer, session_store


def _multipart_files() -> dict[str, tuple[str, io.BytesIO, str]]:
    """A minimal valid PDF multipart body (matches the PHP magic-byte check)."""
    return {"file": ("upload.pdf", io.BytesIO(b"%PDF-1.4\nstub-bytes"), "application/pdf")}


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401() -> None:
    app, _, _ = _build_app()
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files=_multipart_files(),
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_with_no_fhir_user_returns_401() -> None:
    app, _, store = _build_app()
    sid = await _seed_session(store, fhir_user="")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files=_multipart_files(),
        )
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
            "/api/agent/upload",
            data={
                "patient_uuid": "patient-resource-uuid",
                "doc_type": "lab_pdf",
            },
            files={
                "file": (
                    "lab.pdf",
                    io.BytesIO(b"%PDF-1.4\nlab-bytes"),
                    "application/pdf",
                ),
            },
        )

    assert resp.status_code == 200
    assert resp.json() == {"document_id": 555}

    # Writer received the right inputs — patient_uuid forwarded,
    # filename / content / mimetype carried through, JWT non-empty.
    assert writer.captured["patient_uuid"] == "patient-resource-uuid"
    assert writer.captured["filename"] == "lab.pdf"
    assert writer.captured["content"] == b"%PDF-1.4\nlab-bytes"
    assert writer.captured["mimetype"] == "application/pdf"
    assert writer.captured["doc_type"] == "lab_pdf"
    # The JWT is the one minted by InternalJwtMinter for this turn —
    # we don't try to round-trip it, just confirm it's a non-empty
    # bearer-shaped string. The PHP side validates signature + claims.
    jwt = writer.captured["jwt"]
    assert isinstance(jwt, str) and len(jwt) > 0


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf_mimetype() -> None:
    """Allowlist enforcement happens at the BFF, not at the PHP side."""
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
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files={
                "file": (
                    "evil.exe",
                    io.BytesIO(b"MZ\x00\x00"),
                    "application/x-msdownload",
                ),
            },
        )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversize_file() -> None:
    """11 MB exceeds the 10 MB BFF cap → 413."""
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x",
    )

    big = b"%PDF-1.4\n" + (b"A" * (11 * 1024 * 1024))
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files={
                "file": ("big.pdf", io.BytesIO(big), "application/pdf"),
            },
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_doc_type() -> None:
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
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "discharge_summary"},
            files=_multipart_files(),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_writer_transport_failure_maps_to_503() -> None:
    """status_code == 0 from the writer (transport failure) → 503."""
    writer = _CapturingUploadWriter()
    writer.raise_with = DocumentUploadError(
        status_code=0, message="transport failure"
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        upload_writer=writer,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files=_multipart_files(),
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_upload_writer_upstream_error_maps_to_502() -> None:
    """Non-zero status_code from the writer → 502 with upstream status."""
    writer = _CapturingUploadWriter()
    writer.raise_with = DocumentUploadError(
        status_code=403, message="forbidden"
    )
    app, _, store = _build_app(
        me_response={"user_id": 1, "username": "u", "role": None},
        pid_response={"pid": 1},
        upload_writer=writer,
    )
    sid = await _seed_session(
        store,
        fhir_user=f"{OPENEMR_BASE}/fhir/Practitioner/x",
    )

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sid)
        resp = client.post(
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "lab_pdf"},
            files=_multipart_files(),
        )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_upload_request_with_empty_patient_uuid_returns_422() -> None:
    """Pydantic / Form validation rejects empty patient_uuid."""
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
            "/api/agent/upload",
            data={"patient_uuid": "", "doc_type": "lab_pdf"},
            files=_multipart_files(),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_accepts_image_mimetypes() -> None:
    """JPEG and PNG are part of the allowlist (intake form scans)."""
    app, writer, store = _build_app(
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
            "/api/agent/upload",
            data={"patient_uuid": "p", "doc_type": "intake_form"},
            files={
                "file": (
                    "scan.png",
                    io.BytesIO(b"\x89PNG\r\n\x1a\nfake-png"),
                    "image/png",
                ),
            },
        )
    assert resp.status_code == 200
    assert writer.captured["mimetype"] == "image/png"
