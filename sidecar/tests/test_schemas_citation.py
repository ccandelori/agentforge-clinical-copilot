"""Tests for the W2 Citation Pydantic contract.

The Citation model is the contract between extraction (vision tool
output), retrieval (RAG hits), the verifier (claim grounding), and the
UI overlay (page_bbox positioning). Every clinical claim in a final
answer carries one.

The load-bearing invariant is the bbox-confidence floor: scanned-source
citations (``LAB_PDF`` and ``INTAKE_FORM``) must have a ``page_bbox``
with ``bbox_confidence >= 0.7``. Lower-confidence fields can only land
in ``unsupported_fields`` (handled by callers), never as a structured
``Citation``. Guideline citations don't have bboxes — they're text
chunks identified by section / chunk_id.

See W2_ARCHITECTURE.md §2.2 and §2.4.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.schemas.citation import Citation, PageBBox, SourceType

# ---------------------------------------------------------------------------
# SourceType enum
# ---------------------------------------------------------------------------

def test_source_type_has_four_canonical_values() -> None:
    """The enum is the closed set of citation origins. Adding a value is
    a coordinated change across extraction, verifier, and UI — not a
    drive-by."""
    assert SourceType.LAB_PDF.value == "lab_pdf"
    assert SourceType.INTAKE_FORM.value == "intake_form"
    assert SourceType.GUIDELINE.value == "guideline"
    assert SourceType.OPENEMR_RECORD.value == "openemr_record"
    # Exactly four; no others.
    assert {member.value for member in SourceType} == {
        "lab_pdf",
        "intake_form",
        "guideline",
        "openemr_record",
    }


def test_source_type_serializes_as_string() -> None:
    """str-Enum ensures Citation.model_dump() emits the lowercase string,
    not 'SourceType.LAB_PDF'."""
    bbox = PageBBox(page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.95)
    citation = _lab_pdf_citation(bbox=bbox)
    assert citation.model_dump()["source_type"] == "lab_pdf"


# ---------------------------------------------------------------------------
# PageBBox — coordinate + confidence ranges
# ---------------------------------------------------------------------------

def test_pagebbox_accepts_in_range_coords() -> None:
    """All five numeric fields fall within their stated ranges."""
    bbox = PageBBox(page=1, x0=0.0, y0=0.0, x1=1.0, y1=1.0, bbox_confidence=0.85)
    assert bbox.page == 1
    assert bbox.x0 == 0.0
    assert bbox.x1 == 1.0
    assert bbox.bbox_confidence == 0.85


def test_pagebbox_rejects_page_zero() -> None:
    """page is 1-indexed (matches pdf.js native semantics and PDF specs).
    Page zero is a programmer error, not a data-quality issue."""
    with pytest.raises(ValidationError):
        PageBBox(page=0, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)


def test_pagebbox_rejects_negative_page() -> None:
    with pytest.raises(ValidationError):
        PageBBox(page=-1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)


def test_pagebbox_rejects_x_above_one() -> None:
    """Coordinates are normalized to [0, 1]. Anything above is the VLM
    misreading dimensions (or a fabrication) — reject at the schema."""
    with pytest.raises(ValidationError):
        PageBBox(page=1, x0=1.5, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)


def test_pagebbox_rejects_y_below_zero() -> None:
    with pytest.raises(ValidationError):
        PageBBox(page=1, x0=0.1, y0=-0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)


def test_pagebbox_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        PageBBox(page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=1.5)


def test_pagebbox_rejects_negative_confidence() -> None:
    with pytest.raises(ValidationError):
        PageBBox(page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=-0.1)


# ---------------------------------------------------------------------------
# Citation validator: the bbox-confidence floor for scanned sources
# ---------------------------------------------------------------------------

def test_guideline_citation_accepts_missing_bbox() -> None:
    """GUIDELINE citations point at text chunks (section / chunk_id) —
    they have no bbox by design."""
    citation = Citation(
        source_type=SourceType.GUIDELINE,
        source_id="ada-2026-standards-of-care",
        page_or_section="Section 4.1",
        field_or_chunk_id="guideline_chunk_12",
        quote_or_value="A1C target of <7% is appropriate for many nonpregnant adults.",
        page_bbox=None,
    )
    assert citation.page_bbox is None


def test_openemr_record_citation_accepts_missing_bbox() -> None:
    """OPENEMR_RECORD citations identify rows by table id (W1 grammar);
    no bbox applies."""
    citation = Citation(
        source_type=SourceType.OPENEMR_RECORD,
        source_id="42",
        page_or_section="lab_result #42",
        field_or_chunk_id="hemoglobin_a1c",
        quote_or_value="9.5",
        page_bbox=None,
    )
    assert citation.page_bbox is None


def test_lab_pdf_citation_rejects_missing_bbox() -> None:
    """LAB_PDF citations MUST anchor a coordinate. Without one, the
    overlay can't render and the field can't be traced — that's an
    extraction we shouldn't trust as a structured value."""
    with pytest.raises(ValidationError) as exc_info:
        Citation(
            source_type=SourceType.LAB_PDF,
            source_id="document_id_123",
            page_or_section="page 2",
            field_or_chunk_id="hemoglobin_a1c",
            quote_or_value="9.5",
            page_bbox=None,
        )
    # Error mentions bbox so future contributors know what's missing.
    assert "bbox" in str(exc_info.value).lower()


def test_intake_form_citation_rejects_missing_bbox() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Citation(
            source_type=SourceType.INTAKE_FORM,
            source_id="document_id_456",
            page_or_section="page 1",
            field_or_chunk_id="allergies_penicillin",
            quote_or_value="Penicillin",
            page_bbox=None,
        )
    assert "bbox" in str(exc_info.value).lower()


def test_lab_pdf_citation_rejects_low_confidence_bbox() -> None:
    """The 0.7 floor is the load-bearing anti-invention check. A bbox
    with confidence 0.6 is "the VLM thinks it found something here, but
    not strongly" — let it land in unsupported_fields, not as a Citation
    that drives a chart entry."""
    low_confidence = PageBBox(
        page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.6
    )
    with pytest.raises(ValidationError) as exc_info:
        Citation(
            source_type=SourceType.LAB_PDF,
            source_id="document_id_789",
            page_or_section="page 1",
            field_or_chunk_id="hemoglobin_a1c",
            quote_or_value="9.5",
            page_bbox=low_confidence,
        )
    msg = str(exc_info.value).lower()
    assert "0.7" in msg or "confidence" in msg


def test_intake_form_citation_rejects_low_confidence_bbox() -> None:
    low_confidence = PageBBox(
        page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.5
    )
    with pytest.raises(ValidationError):
        Citation(
            source_type=SourceType.INTAKE_FORM,
            source_id="document_id_999",
            page_or_section="page 2",
            field_or_chunk_id="allergies_penicillin",
            quote_or_value="Penicillin",
            page_bbox=low_confidence,
        )


def test_lab_pdf_citation_accepts_threshold_bbox() -> None:
    """Exactly 0.7 is acceptable — the floor is inclusive. Anyone tempted
    to nudge it up to 0.71 should bump the constant in the validator
    explicitly, not via test rewrite."""
    threshold_bbox = PageBBox(
        page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.7
    )
    citation = Citation(
        source_type=SourceType.LAB_PDF,
        source_id="document_id_111",
        page_or_section="page 1",
        field_or_chunk_id="hemoglobin_a1c",
        quote_or_value="9.5",
        page_bbox=threshold_bbox,
    )
    assert citation.page_bbox is not None
    assert citation.page_bbox.bbox_confidence == 0.7


def test_lab_pdf_citation_accepts_high_confidence_bbox() -> None:
    high = PageBBox(page=2, x0=0.2, y0=0.4, x1=0.7, y1=0.5, bbox_confidence=0.95)
    citation = Citation(
        source_type=SourceType.LAB_PDF,
        source_id="document_id_222",
        page_or_section="page 2",
        field_or_chunk_id="creatinine",
        quote_or_value="1.2 mg/dL",
        page_bbox=high,
    )
    assert citation.page_bbox.bbox_confidence == 0.95


def test_intake_form_citation_accepts_high_confidence_bbox() -> None:
    high = PageBBox(page=1, x0=0.2, y0=0.4, x1=0.7, y1=0.5, bbox_confidence=0.88)
    citation = Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="document_id_333",
        page_or_section="page 1",
        field_or_chunk_id="medications_metformin",
        quote_or_value="Metformin 500mg BID",
        page_bbox=high,
    )
    assert citation.page_bbox.bbox_confidence == 0.88


# ---------------------------------------------------------------------------
# Citation field requirements (the rest of the contract)
# ---------------------------------------------------------------------------

def test_citation_requires_all_text_fields() -> None:
    """Every Citation needs source_id / page_or_section / field_or_chunk_id /
    quote_or_value populated. Empty strings are still strings — those are
    a separate concern (caller's responsibility to pass non-empty)."""
    bbox = PageBBox(page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)
    with pytest.raises(ValidationError):
        Citation(
            source_type=SourceType.LAB_PDF,
            # source_id missing
            page_or_section="page 1",
            field_or_chunk_id="hemoglobin_a1c",
            quote_or_value="9.5",
            page_bbox=bbox,
        )  # type: ignore[call-arg]


def test_citation_round_trip_through_model_dump_and_validate() -> None:
    """A Citation that round-trips through dict serialization stays
    structurally identical. This is the contract the verifier relies on
    when deserializing extraction tool results."""
    bbox = PageBBox(page=2, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9)
    original = Citation(
        source_type=SourceType.LAB_PDF,
        source_id="document_id_444",
        page_or_section="page 2",
        field_or_chunk_id="potassium",
        quote_or_value="4.2 mmol/L",
        page_bbox=bbox,
    )
    dumped = original.model_dump()
    rebuilt = Citation.model_validate(dumped)
    assert rebuilt == original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lab_pdf_citation(*, bbox: PageBBox) -> Citation:
    return Citation(
        source_type=SourceType.LAB_PDF,
        source_id="document_id_555",
        page_or_section="page 1",
        field_or_chunk_id="example_field",
        quote_or_value="example value",
        page_bbox=bbox,
    )
