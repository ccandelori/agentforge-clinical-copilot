"""End-to-end intake-form upload + extraction test (Task 29).

This test exercises the full intake-form flow at the *process boundary*:

    PDF bytes → BFF /api/agent/upload → extraction worker → persist

It runs offline. The Anthropic vision client is replaced with an
``AsyncMock`` returning a canned :class:`IntakeFormExtraction` payload.
The PHP-side endpoints (``upload_document.php``,
``persist_questionnaire_response.php``) are stubbed with
``httpx.MockTransport`` handlers that record every URL the sidecar
hits. No real DB, no real Anthropic, no dev-easy stack.

Why a process-level test (and not a real-stack one): the load-bearing
W2 architectural invariant is that intake forms write to
``QuestionnaireResponse`` and *never* to clinical tables
(``patient_data``, ``medications``, ``allergies``, ``family_history``).
That invariant manifests as a routing constraint at the sidecar/PHP
boundary — a regression would show up as the sidecar POSTing to a
clinical-table endpoint. By stubbing PHP with MockTransport we get a
strict allowlist of "URLs the e2e flow may hit" — the test fails
loudly if the route ever expands to a clinical-write endpoint, which
is exactly the case the architecture invariant forbids.

The test does NOT depend on the integration suite's ``conftest.py``
fixtures (the live-OpenEMR ones); pytest only invokes those when a
test asks for them by name. We declare none, so the test file is
self-contained even though it shares the directory.

The companion lab e2e (Task 28) lives in a parallel worktree and
does NOT share this file's fixtures via a conftest — see
:mod:`_intake_e2e_fixtures` for the rationale.

Subsequent commits add the extraction, persistence, and audit-ordering
phases against the same fixture surface.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any
from unittest.mock import AsyncMock

import fitz  # type: ignore[import-untyped]
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
from tests.integration._intake_e2e_fixtures import (
    PINNED_INTAKE_CONTENT,
    build_intake_pdf_bytes,
)

# ---------------------------------------------------------------------------
# Subtask 29.1 — PDF generator smoke test
# ---------------------------------------------------------------------------


def test_intake_pdf_generator_produces_parseable_pdf_with_all_five_sections() -> None:
    """The synthetic intake PDF parses through PyMuPDF and contains
    the five sections Task 29 requires (demographics, chief concern,
    medications, allergies, family history).

    This is the headline acceptance gate for subtask 29.1: if the
    generator ever drops a section or stops emitting valid PDF bytes,
    the e2e flow can't even start.
    """
    pdf_bytes = build_intake_pdf_bytes()

    # Header check — every PDF starts with %PDF-.
    assert pdf_bytes.startswith(b"%PDF-")
    # Generator output should be small but non-trivial. Empty would
    # mean the generator silently dropped its body.
    assert len(pdf_bytes) > 1000

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    # All five Task-29 sections must be present in the rendered form.
    for section in (
        "Patient Information",        # demographics
        "Chief Concern",              # chief concern
        "Current Medications",        # medications
        "Allergies",                  # allergies
        "Family Medical History",     # family history
    ):
        assert section in full_text, f"PDF missing section: {section!r}"

    # And the pinned content must round-trip through the renderer so
    # the canned-extraction values match what the source actually says.
    assert PINNED_INTAKE_CONTENT.chief_concern in full_text
    assert PINNED_INTAKE_CONTENT.medications[0][0] in full_text  # "Metformin"
    assert PINNED_INTAKE_CONTENT.allergies[0][0] in full_text     # "Penicillin"


def test_intake_pdf_generator_is_deterministic() -> None:
    """Two builds with the same content emit byte-identical PDFs.

    ReportLab's ``invariant=1`` flag strips the per-run trailer ID; if
    a future ReportLab upgrade silently changes that behavior, the
    e2e test would start carrying timestamp-shaped flakiness. Pin it
    here so the regression is obvious."""
    a = build_intake_pdf_bytes()
    b = build_intake_pdf_bytes()
    assert a == b


# ---------------------------------------------------------------------------
# Shared scaffolding for subtasks 29.2 onward
# ---------------------------------------------------------------------------


# JWT secret + cookie name kept deliberately short / well-known: this is
# a self-contained test, not a security exercise.
_JWT_SECRET = "a-very-long-test-secret-that-is-at-least-32b"
_SESSION_COOKIE = "agentforge_session"
_OPENEMR_BASE = "https://openemr.example"

# Document and patient identifiers used throughout the flow. They're
# arbitrary positive ints; the relevant invariant is that the same
# document_id flows from upload → extraction → persist with no rebinding.
_DOCUMENT_ID = 4242
_PATIENT_PID = 77
_PATIENT_UUID = "patient-resource-uuid-29"
_USER_UUID = "practitioner-uuid-29"


class _Clock:
    """Trivial wall-clock for the JWT minter / fetchers.

    The dashboard-auth components use a clock object (PSR-20-style
    in spirit) so tests can pin time. Here we don't need pinning —
    we just need *some* now() — so the simplest impl is fine.
    """

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


def _make_redis_mock() -> AsyncMock:
    """In-memory stand-in for the Redis session store.

    SessionStore only needs ``get`` / ``setex`` / ``delete``, so a
    dict-backed AsyncMock satisfies its surface without pulling in a
    real Redis. Lifted from ``test_agent_upload_route.py`` so the test
    environment matches the auth-pipeline fixture exactly.
    """
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


def _settings() -> Settings:
    return Settings(
        anthropic_api_key="test-key-not-real",
        jwt_secret=_JWT_SECRET,
        dashboard_session_cookie_name=_SESSION_COOKIE,
        redis_url="redis://localhost:6379/0",
        hmac_key="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1zZWNyZXQtdGVzdGluZw==",
    )


# ---------------------------------------------------------------------------
# Subtask 29.2 — Upload phase
# ---------------------------------------------------------------------------


class _RecordingUploadWriter:
    """DocumentUploadWriter stand-in that captures the upload call AND
    fires the ``agentforge.document_ingest`` audit event into a shared
    list owned by the test.

    Two responsibilities collapsed into one stub:
      * Behaves like the real writer (returns a document_id)
      * Models the PHP side's audit emission so the e2e test can
        assert audit ordering across the whole flow.

    The PHP-side ``DocumentIngestAuditWriter`` writes
    ``agentforge.document_ingest`` to OpenEMR's ``log`` table after
    the document is stored. We mirror that here at the writer
    boundary because the real PHP isn't running — what we lose in
    realism we gain in deterministic, offline assertions.
    """

    def __init__(
        self,
        *,
        audit_log: list[dict[str, Any]],
        document_id: int = _DOCUMENT_ID,
    ) -> None:
        self._audit_log = audit_log
        self.document_id = document_id
        self.captured: dict[str, Any] = {}

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
        # Mirror the PHP-side audit emission. Event name pinned to the
        # constant defined in DocumentIngestAuditWriter.php.
        self._audit_log.append({
            "event": "agentforge.document_ingest",
            "document_id": self.document_id,
            "patient_uuid": patient_uuid,
            "doc_type": doc_type,
        })
        return self.document_id


def _build_upload_app(
    *,
    audit_log: list[dict[str, Any]],
) -> tuple[FastAPI, _RecordingUploadWriter, SessionStore]:
    """Wire the BFF upload router with mocked /me + /patient_pid + writer.

    Identical scaffolding to ``test_agent_upload_route.py`` so the
    e2e test exercises the same auth pipeline production browsers
    hit. The handlers return canned admin identity / patient pid so
    the JWT minter has stable inputs.
    """
    settings = _settings()
    redis = _make_redis_mock()
    session_store = SessionStore(
        redis_client=redis,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    def me_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "user_id": 17,
                "username": "admin",
                "role": "Administrators",
            },
        )

    def pid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pid": _PATIENT_PID})

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=_OPENEMR_BASE,
        jwt_secret=_JWT_SECRET,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=_OPENEMR_BASE,
        jwt_secret=_JWT_SECRET,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=_JWT_SECRET, clock=_Clock())
    gateway = AuthGateway(jwt_secret=_JWT_SECRET, redis_client=None)

    writer = _RecordingUploadWriter(audit_log=audit_log)

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


async def _seed_session(store: SessionStore) -> str:
    session = await store.create_session(
        sub="oauth-sub-uuid-29",
        access_token="access-tok-29",
        expires_at=9_999_999_999.0,
        fhir_user=f"{_OPENEMR_BASE}/fhir/Practitioner/{_USER_UUID}",
    )
    return session.session_id


@pytest.mark.asyncio
async def test_upload_phase_returns_document_id_and_emits_document_ingest_audit() -> None:
    """Subtask 29.2: the BFF route happy-path round-trips an intake PDF
    upload through the auth pipeline, returns the new ``document_id``,
    and fires the ``agentforge.document_ingest`` audit event."""
    audit_log: list[dict[str, Any]] = []
    app, writer, store = _build_upload_app(audit_log=audit_log)
    sid = await _seed_session(store)

    pdf_bytes = build_intake_pdf_bytes()

    with TestClient(app) as client:
        client.cookies.set(_SESSION_COOKIE, sid)
        response = client.post(
            "/api/agent/upload",
            data={
                "patient_uuid": _PATIENT_UUID,
                "doc_type": "intake_form",
            },
            files={
                "file": (
                    "intake.pdf",
                    io.BytesIO(pdf_bytes),
                    "application/pdf",
                ),
            },
        )

    assert response.status_code == 200
    assert response.json() == {"document_id": _DOCUMENT_ID}

    # Writer received the right inputs — patient_uuid forwarded,
    # doc_type carried through, content is the PDF bytes we sent.
    assert writer.captured["patient_uuid"] == _PATIENT_UUID
    assert writer.captured["doc_type"] == "intake_form"
    assert writer.captured["mimetype"] == "application/pdf"
    assert writer.captured["content"] == pdf_bytes

    # Audit event fired exactly once with the right shape.
    assert len(audit_log) == 1
    ingest_event = audit_log[0]
    assert ingest_event["event"] == "agentforge.document_ingest"
    assert ingest_event["document_id"] == _DOCUMENT_ID
    assert ingest_event["doc_type"] == "intake_form"
