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
from typing import Any, cast
from unittest.mock import AsyncMock

import fitz  # type: ignore[import-untyped]
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from pydantic import ValidationError

from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.schemas.lab import LabPdfExtraction
from agentforge.tools.attach_and_extract import (
    DEFAULT_DPI,
    INTAKE_CONTRACT,
    LAB_CONTRACT,
    PdfRenderer,
    RenderedPage,
    VisionContract,
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


_SAMPLE_INTAKE_PDF_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "data" / "samples" / "sample-intake.pdf"
)


def test_bundled_sample_intake_pdf_renders_without_errors() -> None:
    """Mirror of test_bundled_sample_pdf_renders_without_errors for
    the intake fixture. Guards against the same fixture-rot scenario:
    someone updates generate_mock_intake.py but forgets to re-run it,
    or a future ReportLab/PyMuPDF upgrade breaks the bytes."""
    if not _SAMPLE_INTAKE_PDF_PATH.exists():
        pytest.skip(
            "sample-intake.pdf not committed — run "
            "`uv run python scripts/generate_mock_intake.py`."
        )

    pdf_bytes = _SAMPLE_INTAKE_PDF_PATH.read_bytes()
    pages = PdfRenderer(dpi=72).render_pages(pdf_bytes)
    assert len(pages) >= 1, "sample intake PDF should have at least one page"
    for page in pages:
        assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.pixel_width > 0
        assert page.pixel_height > 0


def test_bundled_sample_intake_pdf_contains_expected_sections() -> None:
    """Sanity check the demo intake fixture still has the headline
    sections we advertise in intake_extraction_demo.py — guards
    against an accidental regeneration that drops content."""
    if not _SAMPLE_INTAKE_PDF_PATH.exists():
        pytest.skip("sample-intake.pdf not committed.")

    document = fitz.open(stream=_SAMPLE_INTAKE_PDF_PATH.read_bytes(), filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    for expected in (
        "DEMO PRIMARY CARE CLINIC",
        "Patient Information",
        "Chief Concern",
        "Current Medications",
        "Allergies",
        "Family Medical History",
        "Metformin",
        "Penicillin",
        "DEMO DATA",
    ):
        assert expected in full_text, (
            f"sample intake PDF missing expected text: {expected!r}"
        )


# ---------------------------------------------------------------------------
# VisionExtractor — happy path + error paths
# ---------------------------------------------------------------------------

def _make_anthropic_response(tool_input: dict[str, Any], tool_name: str) -> Message:
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


def _valid_extraction_payload(*, document_id: int = 42, patient_id: int = 7) -> dict[str, Any]:
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

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        model="claude-sonnet-4-5-20250929",
    )
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

    extractor = VisionExtractor(contract=LAB_CONTRACT, client=client)
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

    extractor = VisionExtractor(contract=LAB_CONTRACT, client=client)
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

    extractor = VisionExtractor(contract=LAB_CONTRACT, client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
        )


@pytest.mark.asyncio
async def test_extractor_rejects_empty_pages_list() -> None:
    extractor = VisionExtractor(contract=LAB_CONTRACT, client=AsyncMock())
    with pytest.raises(ValueError, match="empty"):
        await extractor.extract(pages=[], document_id=42, patient_id=7)


@pytest.mark.asyncio
async def test_extractor_attaches_cost_usd_via_vision_pricing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 27.1: VisionExtractor surfaces ``cost_usd`` on the result.

    The cost is computed from the Anthropic-reported ``usage.input_tokens``
    (which already includes image tokens) and ``usage.output_tokens``
    against the configured vision model's row in PRICING. Pinning the
    droplet's dated alias (``claude-haiku-4-5-20251001``) verifies the
    dated-alias resolver path.
    """
    monkeypatch.setenv("ANTHROPIC_VISION_MODEL", "claude-haiku-4-5-20251001")
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=LAB_CONTRACT, client=client)
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=42,
        patient_id=7,
    )

    # Anthropic usage on the stub response: 1234 input + 456 output.
    # Haiku 4.5: input $0.80/M, output $4/M.
    # Cost = 1234 * 0.80e-6 + 456 * 4e-6 = 0.0009872 + 0.001824 = 0.0028112.
    assert result.cost_usd == pytest.approx(0.0028112, rel=1e-9)


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

    extractor = VisionExtractor(contract=LAB_CONTRACT, client=client)
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


# ---------------------------------------------------------------------------
# Contract sanity — guards against drift between the inline tool spec
# and the corresponding Pydantic schema. These tests don't assert
# every JSON-schema property; they assert the load-bearing constants
# (tool name, source_type const, required-field skeleton) so a rename
# in either side surfaces immediately rather than silently breaking
# the LLM emission contract.
# ---------------------------------------------------------------------------


def _input_schema(contract: VisionContract[Any]) -> dict[str, Any]:
    """Cast the contract's tool_spec input_schema to a dict for ergonomic
    assertion access. The Anthropic SDK types tool_spec entries as
    a TypedDict whose fields surface as ``object`` to mypy, which makes
    nested key access painful — but these are tests asserting against
    constants we wrote ourselves, so a plain cast is the right call."""
    return cast(dict[str, Any], contract.tool_spec["input_schema"])


def test_lab_contract_binds_to_lab_pdf_extraction() -> None:
    assert LAB_CONTRACT.extraction_class is LabPdfExtraction
    assert LAB_CONTRACT.tool_name == "emit_lab_pdf_extraction"
    # source_type pinned to lab_pdf at the spec layer prevents the
    # model from emitting a citation that would later fail
    # SourceType validation.
    schema = _input_schema(LAB_CONTRACT)
    citation_const = (
        schema["properties"]["values"]["items"]["properties"]
        ["citation"]["properties"]["source_type"]["const"]
    )
    assert citation_const == "lab_pdf"


def test_intake_contract_binds_to_intake_form_extraction() -> None:
    assert INTAKE_CONTRACT.extraction_class is IntakeFormExtraction
    assert INTAKE_CONTRACT.tool_name == "emit_intake_form_extraction"
    schema = _input_schema(INTAKE_CONTRACT)
    citation_const = (
        schema["properties"]["medications"]["items"]["properties"]
        ["citation"]["properties"]["source_type"]["const"]
    )
    assert citation_const == "intake_form"


def test_intake_contract_lists_all_four_repeating_sections() -> None:
    """The four list sections in IntakeFormExtraction map 1:1 to the
    canonical FHIR Questionnaire's repeating groups (Task 5 migration).
    Drift between this list and the Pydantic schema would mean the
    extractor emits something the persistence layer can't map."""
    props = _input_schema(INTAKE_CONTRACT)["properties"]
    for required_section in (
        "demographics",
        "medications",
        "allergies",
        "family_history",
    ):
        assert required_section in props, f"missing section: {required_section}"
        assert props[required_section]["type"] == "array"


def test_intake_contract_chief_concern_is_optional() -> None:
    """Both chief_concern and its citation are optional in the
    schema (a blank intake form is valid). Pairing — concern present
    ⇒ citation present — is a worker-layer concern, not a schema one."""
    schema = _input_schema(INTAKE_CONTRACT)
    required = schema.get("required", [])
    assert "chief_concern" not in required
    assert "chief_concern_citation" not in required


# ---------------------------------------------------------------------------
# IntakeFormExtraction extraction — happy path + error paths
# ---------------------------------------------------------------------------


def _valid_intake_payload(*, document_id: int = 99, patient_id: int = 5) -> dict[str, Any]:
    """A minimal valid IntakeFormExtraction wire payload.

    Covers chief_concern (optional, present), one row in each of the
    four lists, and the unsupported_fields surface. Keeps every
    citation at the bbox-confidence floor (0.7+) so the schema's
    scanned-source validator doesn't reject the payload before the
    test assertions run.
    """
    cite = {
        "source_type": "intake_form",
        "source_id": str(document_id),
        "page_or_section": "page 1",
        "field_or_chunk_id": "intake-row",
        "quote_or_value": "—",
        "page_bbox": {
            "page": 1,
            "x0": 0.05,
            "y0": 0.10,
            "x1": 0.45,
            "y1": 0.15,
            "bbox_confidence": 0.85,
        },
    }
    return {
        "document_id": document_id,
        "patient_id": patient_id,
        "chief_concern": "follow-up for diabetes",
        "chief_concern_citation": cite,
        "demographics": [
            {"field": "date_of_birth", "value": "1972-04-12", "citation": cite},
        ],
        "medications": [
            {
                "name": "Metformin",
                "dose": "500 mg",
                "frequency": "BID",
                "citation": cite,
            },
        ],
        "allergies": [
            {
                "substance": "Penicillin",
                "reaction": "rash",
                "severity": None,
                "citation": cite,
            },
        ],
        "family_history": [
            {"relative": "Mother", "condition": "Type 2 diabetes", "citation": cite},
        ],
        "extraction_confidence": 0.88,
        "unsupported_fields": ["smoking_status"],
    }


@pytest.mark.asyncio
async def test_intake_extractor_returns_validated_extraction_on_happy_path() -> None:
    payload = _valid_intake_payload()
    response = _make_anthropic_response(payload, "emit_intake_form_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=99,
        patient_id=5,
    )

    assert isinstance(result.extraction, IntakeFormExtraction)
    assert result.extraction.document_id == 99
    assert result.extraction.patient_id == 5
    assert result.extraction.chief_concern == "follow-up for diabetes"
    assert len(result.extraction.demographics) == 1
    assert len(result.extraction.medications) == 1
    assert len(result.extraction.allergies) == 1
    assert len(result.extraction.family_history) == 1
    assert result.extraction.unsupported_fields == ["smoking_status"]


@pytest.mark.asyncio
async def test_intake_extractor_uses_intake_tool_choice_and_prompt() -> None:
    """Lock the request shape — tool_choice forces intake, system
    prompt is the intake one (not the lab one), image blocks come
    first, the tail text block carries the IDs."""
    payload = _valid_intake_payload()
    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_make_anthropic_response(payload, "emit_intake_form_extraction")
    )

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    await extractor.extract(
        pages=_rendered_pages(2),
        document_id=99,
        patient_id=5,
    )

    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "emit_intake_form_extraction",
    }
    # system prompt must be the intake one, not the lab one — otherwise
    # the model gets contradictory instructions.
    assert "intake" in call_kwargs["system"].lower()
    assert "lab-result" not in call_kwargs["system"]
    user_msg = call_kwargs["messages"][0]
    image_blocks = [b for b in user_msg["content"] if b["type"] == "image"]
    assert len(image_blocks) == 2
    text_blocks = [b for b in user_msg["content"] if b["type"] == "text"]
    assert len(text_blocks) == 1
    assert "document_id = 99" in text_blocks[0]["text"]
    assert "patient_id = 5" in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_intake_extractor_accepts_blank_form() -> None:
    """A blank intake form is a valid extraction — chief_concern can
    be omitted entirely, lists default to empty. The schema permits
    this; the synthesizer surfaces the absence to the clinician."""
    payload = {
        "document_id": 1,
        "patient_id": 1,
        "extraction_confidence": 0.5,
    }
    response = _make_anthropic_response(payload, "emit_intake_form_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=1,
        patient_id=1,
    )

    assert isinstance(result.extraction, IntakeFormExtraction)
    assert result.extraction.chief_concern is None
    assert result.extraction.chief_concern_citation is None
    assert result.extraction.demographics == []
    assert result.extraction.medications == []
    assert result.extraction.allergies == []
    assert result.extraction.family_history == []


@pytest.mark.asyncio
async def test_intake_extractor_raises_when_response_lacks_tool_use_block() -> None:
    response = Message.model_validate({
        "id": "msg_intake_2",
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

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    with pytest.raises(RuntimeError, match="tool_use"):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=1,
            patient_id=1,
        )


@pytest.mark.asyncio
async def test_intake_extractor_raises_validation_error_on_bad_payload() -> None:
    """Missing required field (medication name) — schema-side reject."""
    bad_payload = _valid_intake_payload()
    del bad_payload["medications"][0]["name"]
    response = _make_anthropic_response(bad_payload, "emit_intake_form_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=99,
            patient_id=5,
        )


@pytest.mark.asyncio
async def test_intake_extractor_propagates_low_bbox_confidence_rejection() -> None:
    """Citation contract: intake_form citations require bbox_confidence
    >= 0.7 (SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR). A model emitting a
    lower-confidence citation must fail validation here, not silently
    surface as a click-to-source the clinician can't trust."""
    bad_payload = _valid_intake_payload()
    bad_payload["medications"][0]["citation"]["page_bbox"]["bbox_confidence"] = 0.4
    response = _make_anthropic_response(bad_payload, "emit_intake_form_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=99,
            patient_id=5,
        )


@pytest.mark.asyncio
async def test_intake_extractor_propagates_inverted_bbox_validation_error() -> None:
    """Same inverted-bbox guarantee as the lab path."""
    bad_payload = _valid_intake_payload()
    bad_payload["demographics"][0]["citation"]["page_bbox"]["y1"] = 0.05  # < y0=0.10
    response = _make_anthropic_response(bad_payload, "emit_intake_form_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(contract=INTAKE_CONTRACT, client=client)
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=99,
            patient_id=5,
        )


# ---------------------------------------------------------------------------
# P2-3: VisionExtractor → Langfuse telemetry wiring
#
# Production guarantees:
#   * Successful extracts emit one record_extraction_call(schema_validation="pass")
#     span carrying redacted metadata only — model, page_count, tokens,
#     cost_usd, tool_name, extraction_confidence.
#   * Validation failures emit one record_extraction_call(schema_validation="fail")
#     span BEFORE re-raising the ValidationError so failed extractions
#     show up in Langfuse dashboards alongside successes.
#   * No PHI fields are ever passed into record_extraction_call (no
#     document_id, no patient_id, no extracted text). The telemetry API
#     enforces this structurally — these tests assert the call site
#     passes only the API's PHI-safe parameter set.
#   * Extract works fine when langfuse=None (no telemetry call, no error).
#   * Telemetry exceptions are swallowed — the extraction must succeed
#     even if the Langfuse SDK is unhealthy.
# ---------------------------------------------------------------------------


def _trace_handle_stub() -> Any:
    """Build a minimal TraceHandle stand-in.

    The Protocol only needs ``trace_id`` / ``route_decisions`` / ``eval_outcome``
    for downstream consumers; the extractor itself just hands the handle
    back to ``record_extraction_call`` opaquely. A bare object satisfies
    the Protocol structurally for our mocked telemetry assertions.
    """
    from agentforge.observability.protocols import RouteDecisionRecord

    class _Handle:
        def __init__(self) -> None:
            self.trace_id: str | None = "trace-test-1"
            self.route_decisions: list[RouteDecisionRecord] = []
            self.eval_outcome: str | None = None

    return _Handle()


@pytest.mark.asyncio
async def test_extractor_records_extraction_call_on_successful_lab_path() -> None:
    """P2-3: a successful extract fires record_extraction_call with
    schema_validation='pass' and the metadata Langfuse needs for
    per-trace cost/token rollups. Asserts only the redacted parameter
    set — the API itself enforces no-PHI structurally."""
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    langfuse = AsyncMock()
    # record_extraction_call is sync (the Protocol method has no `async`),
    # so make sure the spy is a plain MagicMock — not an AsyncMock that
    # would silently swallow argument errors.
    from unittest.mock import MagicMock
    langfuse.record_extraction_call = MagicMock(return_value=None)
    handle = _trace_handle_stub()

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=langfuse,
        model="claude-sonnet-4-5-20250929",
    )
    result = await extractor.extract(
        pages=_rendered_pages(2),
        document_id=42,
        patient_id=7,
        trace=handle,
    )

    assert result.extraction.document_id == 42  # smoke: extraction still works
    langfuse.record_extraction_call.assert_called_once()
    call = langfuse.record_extraction_call.call_args
    # Trace handle is positional arg 0.
    assert call.args[0] is handle
    kwargs = call.kwargs
    assert kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert kwargs["tool_name"] == "emit_lab_pdf_extraction"
    assert kwargs["input_tokens"] == 1234
    assert kwargs["output_tokens"] == 456
    assert kwargs["schema_validation"] == "pass"
    assert kwargs["page_count"] == 2
    # The lab fixture sets extraction_confidence=0.92 with 0 unsupported_fields.
    assert kwargs["unsupported_fields_count"] == 0
    assert kwargs["extraction_confidence"] == pytest.approx(0.92, rel=1e-9)
    assert isinstance(kwargs["latency_ms"], int)
    assert kwargs["latency_ms"] >= 0
    # No PHI keys leaked into the call — the function signature enforces
    # this structurally, but assert here so a future contract change
    # surfaces against this site rather than silently breaking the
    # langfuse_client suite.
    forbidden = {"document_id", "patient_id", "extraction", "raw_input", "raw_output"}
    assert not (set(kwargs.keys()) & forbidden), (
        f"PHI-bearing kwargs leaked: {set(kwargs.keys()) & forbidden!r}"
    )


@pytest.mark.asyncio
async def test_extractor_records_extraction_call_on_validation_failure() -> None:
    """P2-3: a ValidationError on the model's tool_use payload still
    emits one telemetry span with schema_validation='fail' BEFORE the
    error propagates. Failed extractions need to show up on dashboards
    so we can spot a regression in the prompt or the model.

    extraction_confidence is None here — validation failed before the
    confidence number was meaningful — and unsupported_fields_count
    is reported as 0 (we never reached the validated extraction)."""
    bad_payload = _valid_extraction_payload()
    del bad_payload["values"][0]["citation"]  # required → ValidationError
    response = _make_anthropic_response(bad_payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    langfuse = AsyncMock()
    from unittest.mock import MagicMock
    langfuse.record_extraction_call = MagicMock(return_value=None)
    handle = _trace_handle_stub()

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=langfuse,
        model="claude-sonnet-4-5-20250929",
    )
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
            trace=handle,
        )

    langfuse.record_extraction_call.assert_called_once()
    kwargs = langfuse.record_extraction_call.call_args.kwargs
    assert kwargs["schema_validation"] == "fail"
    assert kwargs["tool_name"] == "emit_lab_pdf_extraction"
    assert kwargs["page_count"] == 1
    assert kwargs["input_tokens"] == 1234
    assert kwargs["output_tokens"] == 456
    # No confidence value when validation fails before extraction is built.
    assert kwargs.get("extraction_confidence") is None
    assert kwargs["unsupported_fields_count"] == 0


@pytest.mark.asyncio
async def test_extractor_skips_telemetry_when_langfuse_is_none() -> None:
    """P2-3: extract() must work when no langfuse client is wired
    (local-dev / unit-test default). No exception, no spurious calls."""
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=None,
    )
    # Trace is None too — no telemetry path should be entered.
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=42,
        patient_id=7,
        trace=None,
    )

    assert result.extraction.document_id == 42


@pytest.mark.asyncio
async def test_extractor_skips_telemetry_when_trace_is_none() -> None:
    """P2-3: a configured langfuse client without a per-turn trace
    handle (e.g. an out-of-band extraction outside the supervisor turn
    loop) must NOT call record_extraction_call — the span has no parent
    to attach to. Same null-handle guard the other observability sites
    use."""
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    langfuse = AsyncMock()
    from unittest.mock import MagicMock
    langfuse.record_extraction_call = MagicMock(return_value=None)

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=langfuse,
    )
    await extractor.extract(
        pages=_rendered_pages(),
        document_id=42,
        patient_id=7,
        trace=None,
    )

    langfuse.record_extraction_call.assert_not_called()


@pytest.mark.asyncio
async def test_extractor_swallows_telemetry_exception_on_success_path() -> None:
    """P2-3: telemetry must never fail the extraction. If Langfuse is
    unhealthy (network blip, SDK bug, whatever), the extract() result
    is still returned — the user-visible flow is unaffected."""
    payload = _valid_extraction_payload()
    response = _make_anthropic_response(payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    langfuse = AsyncMock()
    from unittest.mock import MagicMock
    langfuse.record_extraction_call = MagicMock(
        side_effect=RuntimeError("langfuse SDK exploded"),
    )
    handle = _trace_handle_stub()

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=langfuse,
    )
    # Should NOT raise — the RuntimeError from the telemetry call is swallowed.
    result = await extractor.extract(
        pages=_rendered_pages(),
        document_id=42,
        patient_id=7,
        trace=handle,
    )
    assert result.extraction.document_id == 42


@pytest.mark.asyncio
async def test_extractor_swallows_telemetry_exception_on_failure_path() -> None:
    """P2-3: even when the failure-path telemetry call itself raises,
    the original ValidationError must propagate — telemetry never
    masks the underlying error."""
    bad_payload = _valid_extraction_payload()
    del bad_payload["values"][0]["citation"]
    response = _make_anthropic_response(bad_payload, "emit_lab_pdf_extraction")
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    langfuse = AsyncMock()
    from unittest.mock import MagicMock
    langfuse.record_extraction_call = MagicMock(
        side_effect=RuntimeError("langfuse SDK exploded"),
    )
    handle = _trace_handle_stub()

    extractor = VisionExtractor(
        contract=LAB_CONTRACT,
        client=client,
        langfuse=langfuse,
    )
    # Original ValidationError still propagates; telemetry exception is swallowed.
    with pytest.raises(ValidationError):
        await extractor.extract(
            pages=_rendered_pages(),
            document_id=42,
            patient_id=7,
            trace=handle,
        )
