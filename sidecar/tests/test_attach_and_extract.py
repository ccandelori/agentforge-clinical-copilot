"""Tests for the W2 Task 11 ``attach_and_extract`` vision tool.

Two layers:

1. **PdfRenderer** — rendering a synthesized PDF round-trips into
   per-page PNG bytes with sane dimensions. No network, no Anthropic.
2. **VisionExtractor** — happy path, validation failure, and missing-
   tool-use error path. Uses an ``AsyncMock`` Anthropic client so
   tests stay offline and deterministic.

Anything tied to the OpenEMR HTTP fetcher / persist endpoint sits one
layer up (the orchestrator wires fetch → render → extract → persist);
this module's tests intentionally don't span that boundary so the
units stay testable without Apache or network.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import fitz  # type: ignore[import-untyped]
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from pydantic import ValidationError

from agentforge.tools.attach_and_extract import (
    DEFAULT_DPI,
    PdfRenderer,
    RenderedPage,
    VisionExtractor,
)

# ---------------------------------------------------------------------------
# PdfRenderer
# ---------------------------------------------------------------------------

def _synthesize_pdf(num_pages: int) -> bytes:
    """Build a small PDF in memory using PyMuPDF itself — no
    reportlab dep — and return its bytes. Useful for renderer tests
    that need a real PDF without committing fixture files."""
    doc = fitz.open()
    try:
        for i in range(num_pages):
            page = doc.new_page(width=612, height=792)  # US Letter, points
            page.insert_text((72, 72), f"Synthesized lab page {i + 1}")
            page.insert_text((72, 100), "Glucose 180 mg/dL  HIGH")
        return bytes(doc.tobytes())
    finally:
        doc.close()


def test_renderer_rejects_empty_bytes() -> None:
    with pytest.raises(ValueError, match="empty"):
        PdfRenderer().render_pages(b"")


def test_renderer_rejects_non_pdf_bytes() -> None:
    """Mis-classified bytes (e.g. a JPEG stored with a .pdf
    extension upstream) should fail loudly — better than a partial
    render or a fitz null-pointer somewhere down the call stack."""
    with pytest.raises(ValueError, match="failed to open PDF"):
        PdfRenderer().render_pages(b"not a pdf at all")


def test_renderer_returns_one_page_for_single_page_pdf() -> None:
    pdf = _synthesize_pdf(1)
    pages = PdfRenderer().render_pages(pdf)
    assert len(pages) == 1
    assert pages[0].page_number == 1


def test_renderer_returns_two_pages_for_two_page_pdf() -> None:
    """Test Strategy #1. Multi-page round-trip."""
    pdf = _synthesize_pdf(2)
    pages = PdfRenderer().render_pages(pdf)
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[1].page_number == 2


def test_renderer_produces_valid_png_bytes_for_each_page() -> None:
    """PNG header check — 8-byte signature \\x89PNG\\r\\n\\x1a\\n.
    A valid render should emit bytes that start with the PNG magic;
    if PyMuPDF ever changes its default, this catches it loudly."""
    pages = PdfRenderer().render_pages(_synthesize_pdf(1))
    assert pages[0].png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(pages[0].png_bytes) > 100, "PNG suspiciously small"


def test_renderer_pages_round_trip_through_pil_ish_decode() -> None:
    """Decode the PNG bytes back through fitz.Pixmap to confirm they
    represent a non-empty image. Avoids adding Pillow as a dev dep
    while still exercising the bytes."""
    pages = PdfRenderer().render_pages(_synthesize_pdf(1))
    decoded = fitz.Pixmap(io.BytesIO(pages[0].png_bytes).read())
    try:
        assert decoded.width == pages[0].pixel_width
        assert decoded.height == pages[0].pixel_height
        assert decoded.width > 0 and decoded.height > 0
    finally:
        # Pixmap doesn't implement context manager in all fitz versions
        del decoded


def test_renderer_dpi_setting_changes_pixel_dimensions() -> None:
    """At 72 DPI a 612x792-point letter page renders ~612x792 pixels;
    at 150 DPI it's ~1275x1650. Ensures the dpi knob is wired through."""
    pdf = _synthesize_pdf(1)
    low = PdfRenderer(dpi=72).render_pages(pdf)[0]
    high = PdfRenderer(dpi=DEFAULT_DPI).render_pages(pdf)[0]
    assert high.pixel_width > low.pixel_width
    assert high.pixel_height > low.pixel_height


def test_renderer_rejects_non_positive_dpi() -> None:
    with pytest.raises(ValueError, match="dpi"):
        PdfRenderer(dpi=0)
    with pytest.raises(ValueError, match="dpi"):
        PdfRenderer(dpi=-1)


# ---------------------------------------------------------------------------
# Sample PDF fixture — guards against the committed mock-lab PDF
# rotting silently
# ---------------------------------------------------------------------------

import pathlib  # noqa: E402

_SAMPLE_PDF_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "data" / "samples" / "sample-lab.pdf"
)


def test_bundled_sample_pdf_renders_without_errors() -> None:
    """The committed sample PDF (generated by
    ``scripts/generate_mock_lab.py``) must remain renderable. Catches
    a fixture rot scenario where someone updates the generator script
    but forgets to re-run it, or where a future ReportLab/PyMuPDF
    upgrade breaks the bytes."""
    if not _SAMPLE_PDF_PATH.exists():
        pytest.skip(
            "sample-lab.pdf not committed — run "
            "`uv run python scripts/generate_mock_lab.py`."
        )

    pdf_bytes = _SAMPLE_PDF_PATH.read_bytes()
    pages = PdfRenderer(dpi=72).render_pages(pdf_bytes)
    assert len(pages) >= 1, "sample PDF should have at least one page"
    for page in pages:
        assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.pixel_width > 0
        assert page.pixel_height > 0


def test_bundled_sample_pdf_contains_expected_panels() -> None:
    """Sanity check that the demo fixture still has the four panels
    we advertise in extraction_demo.py — guards against an
    accidental regeneration that drops content."""
    if not _SAMPLE_PDF_PATH.exists():
        pytest.skip("sample-lab.pdf not committed.")

    document = fitz.open(stream=_SAMPLE_PDF_PATH.read_bytes(), filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    for expected in (
        "Diabetes Monitoring",
        "Comprehensive Metabolic Panel",
        "Lipid Panel",
        "Complete Blood Count",
        "Hemoglobin A1c",
        "DEMO DATA",
    ):
        assert expected in full_text, f"sample PDF missing expected text: {expected!r}"


# ---------------------------------------------------------------------------
# VisionExtractor — happy path + error paths
# ---------------------------------------------------------------------------

def _make_anthropic_response(tool_input: dict, tool_name: str) -> Message:
    """Build a fake anthropic.types.Message that simulates a vision
    tool-use response. We construct it through the SDK types so any
    Pydantic-side renames in the SDK surface immediately as a test
    failure (rather than letting the production code drift)."""
    return Message.model_validate({
        "id": "msg_test_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5-20250929",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test_1",
                "name": tool_name,
                "input": tool_input,
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1234,
            "output_tokens": 456,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "service_tier": "standard",
            "server_tool_use": None,
        },
    })


def _valid_extraction_payload(*, document_id: int = 42, patient_id: int = 7) -> dict:
    return {
        "document_id": document_id,
        "patient_id": patient_id,
        "ordering_provider": "Dr. Smith",
        "accession_number": "ACC-1",
        "values": [
            {
                "test_name": "Glucose",
                "loinc_code": "2345-7",
                "value": "180",
                "unit": "mg/dL",
                "reference_range": "70-100",
                "collection_date": "2026-04-15",
                "abnormal_flag": "high",
                "citation": {
                    "source_type": "lab_pdf",
                    "source_id": str(document_id),
                    "page_or_section": "page 1",
                    "field_or_chunk_id": "glucose",
                    "quote_or_value": "180 mg/dL",
                    "page_bbox": {
                        "page": 1,
                        "x0": 0.1,
                        "y0": 0.2,
                        "x1": 0.5,
                        "y1": 0.3,
                        "bbox_confidence": 0.9,
                    },
                },
            }
        ],
        "extraction_confidence": 0.92,
        "unsupported_fields": [],
    }


def _rendered_pages(n: int = 1) -> list[RenderedPage]:
    return [
        RenderedPage(
            page_number=i + 1,
            png_bytes=b"\x89PNG\r\n\x1a\n_fake_",
            pixel_width=1275,
            pixel_height=1650,
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_extractor_returns_validated_extraction_on_happy_path() -> None:
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(client=client, model="claude-sonnet-4-5-20250929")
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=42,
        patient_id=7,
    )

    assert result.extraction.document_id == 42
    assert result.extraction.patient_id == 7
    assert len(result.extraction.values) == 1
    assert result.extraction.values[0].test_name == "Glucose"
    assert result.input_tokens == 1234
    assert result.output_tokens == 456
    assert result.model == "claude-sonnet-4-5-20250929"


@pytest.mark.asyncio
async def test_extractor_calls_anthropic_with_image_blocks_and_tool_choice() -> None:
    """Lock the request shape — tool_choice forces structured output,
    image blocks come first, the tail text block carries the IDs."""
    payload = _valid_extraction_payload()
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_anthropic_response(payload, "emit_lab_pdf_extraction")
    )

    extractor = VisionExtractor(client=client)
    await extractor.extract(
        pages=_rendered_pages(2),
        document_id=42,
        patient_id=7,
    )

    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "emit_lab_pdf_extraction",
    }
    user_msg = call_kwargs["messages"][0]
    assert user_msg["role"] == "user"
    image_blocks = [b for b in user_msg["content"] if b["type"] == "image"]
    assert len(image_blocks) == 2
    text_blocks = [b for b in user_msg["content"] if b["type"] == "text"]
    assert len(text_blocks) == 1
    assert "document_id = 42" in text_blocks[0]["text"]
    assert "patient_id = 7" in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_extractor_raises_when_response_lacks_tool_use_block() -> None:
    """If the model emits free text instead of calling the tool — a
    refusal scenario or a model bug — fail loudly so the caller routes
    to a 5xx instead of a silent zero-extraction. Built without a
    Usage object so the validate path doesn't get confused."""
    response = Message.model_validate({
        "id": "msg_test_2",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5-20250929",
        "content": [{"type": "text", "text": "I cannot extract that."}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "service_tier": "standard",
            "server_tool_use": None,
        },
    })
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(client=client)
    with pytest.raises(RuntimeError, match="tool_use"):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
        )


@pytest.mark.asyncio
async def test_extractor_raises_validation_error_on_bad_payload() -> None:
    """A model-emitted tool_use with the wrong shape (missing
    required field) fails Pydantic validation. The catch in the
    extractor logs a metadata-only warning and re-raises."""
    bad_payload = _valid_extraction_payload()
    del bad_payload["values"][0]["citation"]  # required
    response = _make_anthropic_response(bad_payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
        )


@pytest.mark.asyncio
async def test_extractor_rejects_empty_pages_list() -> None:
    extractor = VisionExtractor(client=AsyncMock())
    with pytest.raises(ValueError, match="empty"):
        await extractor.extract(pages=[], document_id=42, patient_id=7)


@pytest.mark.asyncio
async def test_extractor_propagates_inverted_bbox_validation_error() -> None:
    """The schema-level inverted-bbox check (the P2 fix landed in MR
    !11) must propagate — a model emitting x1 <= x0 should fail
    Pydantic validation, not silently produce a dead overlay box."""
    bad_payload = _valid_extraction_payload()
    bad_payload["values"][0]["citation"]["page_bbox"]["x1"] = 0.05  # < x0=0.1
    response = _make_anthropic_response(bad_payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
        )


# Ensure `TextBlock` import is exercised at least once for any future
# refactor that drops it from the production module.
def test_textblock_import_exercise() -> None:
    block = TextBlock(type="text", text="ok", citations=[])
    assert isinstance(block, TextBlock)


# Ditto ToolUseBlock — kept so its public surface gets test contact.
def test_tooluseblock_import_exercise() -> None:
    block = ToolUseBlock(type="tool_use", id="t1", name="n", input={})
    assert isinstance(block, ToolUseBlock)


# Lock the Usage import so SDK-level renames surface as test failures.
def test_usage_construction_smoke() -> None:
    u = Usage(
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        service_tier="standard",
        server_tool_use=None,
    )
    assert u.input_tokens == 1
