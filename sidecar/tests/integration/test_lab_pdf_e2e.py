"""End-to-end happy-path test for the lab-PDF upload + extraction +
persist flow (Task 28).

**Mock-vs-real-stack decision: Shape (b), process-level integration.**
The brief flags either (a) full live-stack against docker-compose +
real Anthropic, or (b) process-level integration with mocked LLM and
mocked PHP boundary that still exercises the real Pydantic schemas,
real ``DocumentUploadWriter``, real ``VisionExtractor``, and real
``PdfRenderer`` code paths. We pick (b):

  * ``conftest.py`` already gates the live-stack tests on a reachable
    OpenEMR — those tests skip cleanly when the stack is down. Adding
    a sibling test that *requires* the stack would mean the suite
    becomes "all live or all mocked" rather than "skip what we can't
    run", regressing the existing CI ergonomics.
  * Anthropic calls cost real tokens. The brief explicitly says no
    token spend by default; ``@pytest.mark.slow`` is the explicit
    opt-in for token-spending tests, and we don't want this on the
    default path.
  * The PHP-side persist controller has its own PHPUnit test
    (``InternalLabPersistController``) — exercising it from Python
    here would duplicate that coverage, not extend it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import fitz  # type: ignore[import-untyped]
import httpx
import pytest
from anthropic.types import Message

from agentforge.schemas.lab import AbnormalFlag, LabPdfExtraction
from agentforge.tools.attach_and_extract import (
    LAB_CONTRACT,
    PdfRenderer,
    VisionExtractor,
)
from agentforge.tools.document_upload import DocumentUploadWriter

from ._lab_e2e_fixtures import (
    DEFAULT_A1C,
    DEFAULT_CBC_NORMAL,
    DEFAULT_CMP_ONE_FLAGGED,
    CapturingAuditRecorder,
    CapturingLabPersistWriter,
    build_lab_pdf_bytes,
    lab_extraction_payload,
)


# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_DOCUMENT_ID = 7777
_PATIENT_ID = 42
_PATIENT_UUID = "patient-resource-uuid-test"
_FAKE_JWT = "header.payload.signature"


# ---------------------------------------------------------------------------
# 28.1 — synthetic PDF round-trips through PyMuPDF
# ---------------------------------------------------------------------------


def test_synthetic_lab_pdf_renders_through_pymupdf() -> None:
    """Smoke test: the generator emits bytes that PyMuPDF can open and
    page-render. Establishes the foundation for the rest of the flow:
    if the PDF doesn't render, every later step is moot."""
    pdf_bytes = build_lab_pdf_bytes()
    assert pdf_bytes.startswith(b"%PDF"), "output must be a real PDF"

    pages = PdfRenderer(dpi=72).render_pages(pdf_bytes)
    assert len(pages) >= 1
    for page in pages:
        assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.pixel_width > 0
        assert page.pixel_height > 0


def test_synthetic_lab_pdf_contains_all_expected_panels() -> None:
    """The committed default (CBC normal + CMP w/ one flagged + A1c)
    must keep the headline panel/test names readable inside the PDF
    text layer. The vision tool runs on the rendered PNG, but
    asserting on the text layer here gives us a fast contract check
    — if the generator stops emitting "Hemoglobin A1c" the test fails
    immediately rather than waiting on a vision-mock divergence."""
    pdf_bytes = build_lab_pdf_bytes()
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    for expected in (
        "Diabetes Monitoring",
        "Comprehensive Metabolic Panel",
        "Complete Blood Count",
        "Hemoglobin A1c",
        "9.2",  # A1c value
        "BUN",
        "WBC",
        "ACC-2026-05-08-T28",  # accession
    ):
        assert expected in full_text, f"missing expected text: {expected!r}"


def test_synthetic_lab_pdf_is_parameterizable() -> None:
    """The brief's reusability requirement: panels accept overrides.

    Confirms that swapping the A1c row produces a PDF where the new
    value lands in the text layer. Other tests that need a different
    profile (e.g. critical potassium) lean on this knob."""
    from tests.integration._lab_e2e_fixtures import LabRowSpec

    custom_a1c = LabRowSpec(
        "Hemoglobin A1c", "4548-4", "5.4", "%", "<5.7", ""
    )
    pdf_bytes = build_lab_pdf_bytes(a1c_row=custom_a1c)
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    assert "5.4" in full_text
    assert "9.2" not in full_text  # default A1c was overridden


# ---------------------------------------------------------------------------
# 28.2 — upload phase (real DocumentUploadWriter, mocked PHP)
# ---------------------------------------------------------------------------


def _make_upload_transport() -> tuple[httpx.MockTransport, dict[str, Any]]:
    """Build a MockTransport that mimics the OpenEMR upload endpoint.

    Captures the request so we can assert the writer forwarded the
    multipart body verbatim. Returns ``{"document_id": int}`` to mimic
    the real PHP response shape. The captured dict is mutated by the
    handler so the test can read the bytes back after the call.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["authorization"] = request.headers.get("authorization")
        # request.content is the raw multipart body — we don't parse
        # it back into fields here; the writer test
        # (test_tools_document_upload.py) covers that surface.
        captured["body_len"] = len(request.content)
        return httpx.Response(200, json={"document_id": _DOCUMENT_ID})

    return httpx.MockTransport(handler), captured


@pytest.mark.asyncio
async def test_upload_phase_returns_document_id_and_records_audit() -> None:
    """The upload phase produces a numeric document_id and records a
    ``document_ingest`` audit event with the right structural metadata."""
    pdf_bytes = build_lab_pdf_bytes()
    transport, captured = _make_upload_transport()
    audit = CapturingAuditRecorder()

    async with httpx.AsyncClient(transport=transport) as client:
        writer = DocumentUploadWriter(
            base_url="https://openemr.test", http_client=client
        )
        document_id = await writer.upload(
            jwt=_FAKE_JWT,
            patient_uuid=_PATIENT_UUID,
            filename="lab.pdf",
            content=pdf_bytes,
            mimetype="application/pdf",
            doc_type="lab_pdf",
        )

    # Real production code path returned a positive integer document_id.
    assert document_id == _DOCUMENT_ID
    # The MockTransport saw the request — confirms we exercised the
    # writer's HTTP path, not a short-circuit.
    assert captured["method"] == "POST"
    assert captured["authorization"] == f"Bearer {_FAKE_JWT}"
    assert captured["body_len"] >= len(pdf_bytes)

    # Audit-event recording is the test's responsibility — the writer
    # itself doesn't emit; the BFF route would. We simulate that here
    # so the ordering assertion in 28.5 has both events to compare.
    audit.record_document_ingest(
        document_id=document_id,
        patient_id=_PATIENT_ID,
        doc_type="lab_pdf",
        byte_count=len(pdf_bytes),
    )

    assert audit.event_names == ["document_ingest"]
    event = audit.events[0] if audit.events else None
    assert event is not None
    assert event.payload["document_id"] == _DOCUMENT_ID
    assert event.payload["patient_id"] == _PATIENT_ID
    assert event.payload["doc_type"] == "lab_pdf"
    assert event.payload["byte_count"] == len(pdf_bytes)


# ---------------------------------------------------------------------------
# 28.3 — extraction phase (real VisionExtractor, mocked Anthropic)
# ---------------------------------------------------------------------------


def _build_anthropic_message(payload: dict[str, Any]) -> Message:
    """Build a fake :class:`Message` whose tool_use block is the given
    payload. Going through the SDK's Pydantic types so a future SDK
    rename surfaces here, not in production."""
    return Message.model_validate({
        "id": "msg_lab_e2e_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_lab_e2e_1",
                "name": LAB_CONTRACT.tool_name,
                "input": payload,
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1500,
            "output_tokens": 800,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "service_tier": "standard",
            "server_tool_use": None,
        },
    })


@pytest.mark.asyncio
async def test_extraction_phase_validates_canned_payload() -> None:
    """The mock Anthropic response round-trips through ``VisionExtractor``,
    validates as :class:`LabPdfExtraction`, and carries the pinned values."""
    pdf_bytes = build_lab_pdf_bytes()
    pages = PdfRenderer(dpi=72).render_pages(pdf_bytes)

    payload = lab_extraction_payload(
        document_id=_DOCUMENT_ID, patient_id=_PATIENT_ID
    )
    fake_anthropic = AsyncMock()
    fake_anthropic.messages.create = AsyncMock(
        return_value=_build_anthropic_message(payload)
    )

    extractor: VisionExtractor[LabPdfExtraction] = VisionExtractor(
        contract=LAB_CONTRACT, client=fake_anthropic
    )
    result = await extractor.extract(
        pages=pages, document_id=_DOCUMENT_ID, patient_id=_PATIENT_ID
    )

    extraction = result.extraction
    assert isinstance(extraction, LabPdfExtraction)
    assert extraction.document_id == _DOCUMENT_ID
    assert extraction.patient_id == _PATIENT_ID

    # Pin the per-row counts so a regression in the panel-default
    # constants surfaces as a structural assertion failure here.
    expected_count = 1 + len(DEFAULT_CMP_ONE_FLAGGED) + len(DEFAULT_CBC_NORMAL)
    assert len(extraction.values) == expected_count

    # Find A1c specifically and confirm its high flag + value survived.
    a1c = next(v for v in extraction.values if v.test_name == DEFAULT_A1C.test_name)
    assert a1c.value == DEFAULT_A1C.value
    assert a1c.abnormal_flag == AbnormalFlag.HIGH

    # CBC rows should all carry NORMAL after extraction (the default
    # CBC panel has no flagged rows). One quick sanity assert is enough
    # — the per-row mapping is exercised by lab_extraction_payload.
    cbc_test_names = {r.test_name for r in DEFAULT_CBC_NORMAL}
    cbc_extracted = [
        v for v in extraction.values if v.test_name in cbc_test_names
    ]
    assert all(v.abnormal_flag == AbnormalFlag.NORMAL for v in cbc_extracted)


# ---------------------------------------------------------------------------
# 28.4 — persist phase (mock writer captures the validated extraction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_phase_writes_one_result_per_value_with_document_id() -> None:
    """The validated extraction lands at the persist boundary; the fake
    writer captures it and returns one procedure_result id per analyte
    plus the document_id stamped through unchanged. The test asserts:

      * one procedure_result id per LabValue
      * document_id on the persist result equals the upload's
      * a ``lab_persist`` audit event records both the order id and
        the result-id list
    """
    payload = lab_extraction_payload(
        document_id=_DOCUMENT_ID, patient_id=_PATIENT_ID
    )
    extraction = LabPdfExtraction.model_validate(payload)

    persist_writer = CapturingLabPersistWriter()
    audit = CapturingAuditRecorder()

    persist_result = await persist_writer.persist(extraction=extraction)

    assert persist_result.document_id == _DOCUMENT_ID
    assert len(persist_result.procedure_result_ids) == len(extraction.values)

    # Every persisted procedure_result row carries the same document_id —
    # this is the load-bearing invariant for the lab-list inbox UI
    # ("show docs whose results were derived from this PDF").
    audit.record_lab_persist(
        document_id=persist_result.document_id,
        patient_id=_PATIENT_ID,
        procedure_order_id=persist_result.procedure_order_id,
        procedure_result_ids=persist_result.procedure_result_ids,
        extraction_status=(
            "completed" if not extraction.unsupported_fields else "partial"
        ),
    )

    assert audit.event_names == ["lab_persist"]
    event = audit.events[0] if audit.events else None
    assert event is not None
    assert event.payload["document_id"] == _DOCUMENT_ID
    assert (
        event.payload["procedure_order_id"]
        == persist_result.procedure_order_id
    )
    assert event.payload["procedure_result_ids"] == list(
        persist_result.procedure_result_ids
    )
    assert event.payload["extraction_status"] == "completed"

    # The persist boundary recorded the extraction it received — confirms
    # the test exercised the writer's contract instead of short-circuiting.
    assert persist_writer.captured is not None
    assert len(persist_writer.captured) == 1
    captured_extraction = persist_writer.captured[0]
    assert captured_extraction.document_id == _DOCUMENT_ID
    assert len(captured_extraction.values) == len(extraction.values)
