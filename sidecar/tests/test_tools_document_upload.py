"""Tests for :class:`DocumentUploadWriter` (T38.15 piece 2).

Mirror image of test_tools_document_bytes.py: this writer POSTs
multipart bytes to OpenEMR's JWT-authed
``InternalUploadDocumentController`` and returns the new
``document_id`` on success. The contract under test is "what URL,
what multipart fields, what auth header, and how do non-2xx
responses surface as typed errors so the BFF route can map them to
HTTP statuses the browser can act on".
"""

from __future__ import annotations

import httpx
import pytest

from agentforge.tools.document_upload import (
    DocumentUploadError,
    DocumentUploadWriter,
)

BASE_URL = "https://openemr.test"
PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/upload_document.php"
)


def _make_writer(handler: httpx.MockTransport) -> DocumentUploadWriter:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return DocumentUploadWriter(base_url=BASE_URL, http_client=client)


async def test_upload_posts_multipart_and_returns_document_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization")
        # Read the multipart body so we can assert it carries the
        # expected fields. ``Content-Type`` will include the
        # multipart boundary; capture both for assertions.
        captured["content_type"] = request.headers.get("Content-Type")
        captured["body"] = request.content
        return httpx.Response(200, json={"document_id": 123})

    writer = _make_writer(httpx.MockTransport(handler))

    result = await writer.upload(
        jwt="user-jwt-xyz",
        patient_uuid="patient-resource-uuid",
        filename="lab.pdf",
        content=b"%PDF-1.4\nstub",
        mimetype="application/pdf",
        doc_type="lab_pdf",
    )

    assert result == 123
    assert captured["method"] == "POST"
    assert captured["url"] == f"{BASE_URL}{PATH}"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    # Multipart envelope marker; the boundary value itself is httpx-
    # generated so we only assert the type prefix.
    assert isinstance(captured["content_type"], str)
    assert captured["content_type"].startswith("multipart/form-data")
    body = captured["body"]
    assert isinstance(body, bytes)
    # Field markers from python-multipart's encoder. The values land
    # as raw bytes between the boundary lines.
    assert b'name="patient_uuid"' in body
    assert b"patient-resource-uuid" in body
    assert b'name="doc_type"' in body
    assert b"lab_pdf" in body
    assert b'name="file"' in body
    assert b'filename="lab.pdf"' in body
    assert b"%PDF-1.4\nstub" in body


async def test_upload_forwards_optional_encounter_id() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"document_id": 7})

    writer = _make_writer(httpx.MockTransport(handler))

    await writer.upload(
        jwt="jwt",
        patient_uuid="p",
        filename="f.pdf",
        content=b"%PDF-1.4",
        mimetype="application/pdf",
        doc_type="intake_form",
        encounter_id=42,
    )

    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'name="encounter_id"' in body
    assert b"42" in body


async def test_upload_omits_encounter_id_when_not_supplied() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"document_id": 1})

    writer = _make_writer(httpx.MockTransport(handler))

    await writer.upload(
        jwt="jwt",
        patient_uuid="p",
        filename="f.pdf",
        content=b"%PDF-1.4",
        mimetype="application/pdf",
        doc_type="lab_pdf",
    )

    body = captured["body"]
    assert isinstance(body, bytes)
    # No encounter_id field at all when caller omits it — keeps the
    # PHP side from having to special-case empty-string vs missing.
    assert b'name="encounter_id"' not in body


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_upload_raises_typed_error_on_non_2xx(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    writer = _make_writer(httpx.MockTransport(handler))

    with pytest.raises(DocumentUploadError) as exc_info:
        await writer.upload(
            jwt="jwt",
            patient_uuid="p",
            filename="f.pdf",
            content=b"%PDF-1.4",
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )

    assert exc_info.value.status_code == status


async def test_upload_wraps_transport_failures_with_status_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    writer = _make_writer(httpx.MockTransport(handler))

    with pytest.raises(DocumentUploadError) as exc_info:
        await writer.upload(
            jwt="jwt",
            patient_uuid="p",
            filename="f.pdf",
            content=b"%PDF-1.4",
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )

    # status_code == 0 signals "request never reached the server" so
    # the BFF route can map it to a 503 instead of a generic 502.
    assert exc_info.value.status_code == 0


async def test_upload_raises_when_response_lacks_document_id() -> None:
    """A 200 with a malformed body is a server contract violation; the
    writer raises rather than returning a bogus id."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unrelated": "field"})

    writer = _make_writer(httpx.MockTransport(handler))

    with pytest.raises(DocumentUploadError) as exc_info:
        await writer.upload(
            jwt="jwt",
            patient_uuid="p",
            filename="f.pdf",
            content=b"%PDF-1.4",
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )

    # Unparseable success body → status_code 0 so callers treat it
    # the same as a transport failure (don't trust the upstream).
    assert exc_info.value.status_code == 0


@pytest.mark.parametrize("bad", ["", "  "])
async def test_upload_rejects_blank_patient_uuid(bad: str) -> None:
    writer = _make_writer(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"document_id": 1}))
    )

    with pytest.raises(ValueError, match="patient_uuid"):
        await writer.upload(
            jwt="jwt",
            patient_uuid=bad,
            filename="f.pdf",
            content=b"%PDF-1.4",
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )


async def test_upload_rejects_empty_content() -> None:
    writer = _make_writer(
        httpx.MockTransport(lambda r: httpx.Response(200, json={"document_id": 1}))
    )

    with pytest.raises(ValueError, match="content"):
        await writer.upload(
            jwt="jwt",
            patient_uuid="p",
            filename="f.pdf",
            content=b"",
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )
