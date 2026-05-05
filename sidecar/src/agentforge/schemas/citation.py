"""W2 citation contract.

Every clinical claim in a final answer carries a :class:`Citation`
attached. The contract distinguishes patient-record facts from
guideline evidence at the type level via :class:`SourceType`, and
enforces a bounding-box confidence floor on scanned-source citations
(``LAB_PDF`` and ``INTAKE_FORM``) at the schema layer.

Why the floor lives here, not in the prompt:
The vision tool returns geometry with a confidence — sometimes the VLM
"thinks it found the field somewhere" but isn't sure. Promising the
clinician a click-to-source overlay backed by a 0.4-confidence bbox
would trade trust for surface area. Lower-confidence fields belong in
``unsupported_fields`` (handled by the extractor's caller), not as a
structured value.

The W1 ``[record_type #id]`` citation grammar in
:mod:`agentforge.verifier.citation` is a separate, complementary
artifact — it parses citations out of the LLM's text output. This
:class:`Citation` is the structured, schema-validated form that travels
between extraction tools, the synthesizer, and the UI.

See W2_ARCHITECTURE.md §2.2 and §2.4.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class SourceType(StrEnum):
    """Closed set of citation origins.

    Adding a value is a coordinated change across extraction (which
    emits Citations), the verifier (which validates them against tool
    results), and the UI (which renders them differently per type —
    "From this patient's chart…" vs "Per [guideline]…"). It's not a
    drive-by addition.
    """

    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"
    GUIDELINE = "guideline"
    OPENEMR_RECORD = "openemr_record"


class PageBBox(BaseModel):
    """Normalized 0..1 bounding box on a 1-indexed PDF page.

    Coordinates are top-left origin (matches PDF.js and the W2 overlay
    component's pixel-positioning math). bbox_confidence is the VLM's
    stated confidence in the geometric box itself, distinct from
    extraction confidence on the field's textual value.

    Inverted or zero-area rectangles (x1 <= x0 or y1 <= y0) are rejected
    at the schema layer — they're geometrically nonsense, almost always
    a transposed-corner bug or a fabrication, and would render as an
    empty/inverted overlay either way.
    """

    page: int = Field(ge=1, description="1-indexed PDF page number")
    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    bbox_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="VLM-reported confidence in the geometric box [0, 1].",
    )

    @model_validator(mode="after")
    def _reject_inverted_or_zero_area_box(self) -> PageBBox:
        if self.x1 <= self.x0:
            raise ValueError(
                f"PageBBox x1 ({self.x1}) must be strictly greater than "
                f"x0 ({self.x0}); inverted or zero-width boxes are rejected."
            )
        if self.y1 <= self.y0:
            raise ValueError(
                f"PageBBox y1 ({self.y1}) must be strictly greater than "
                f"y0 ({self.y0}); inverted or zero-height boxes are rejected."
            )
        return self


# Inclusive lower bound — bumping this is an explicit, audited change.
# Lower-confidence fields land in `unsupported_fields`, not as structured
# Citations. See W2_ARCHITECTURE.md §8 risk #1.
SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR = 0.7


class Citation(BaseModel):
    """The W2 citation contract — required by every clinical claim.

    Scanned-source citations (``LAB_PDF`` and ``INTAKE_FORM``) must
    carry a :class:`PageBBox` with ``bbox_confidence`` at or above
    :data:`SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR`. ``GUIDELINE`` and
    ``OPENEMR_RECORD`` citations identify text chunks or DB rows; they
    don't have bboxes by design.
    """

    source_type: SourceType
    source_id: str
    """document_id (scanned), guideline doc id, or openemr record id."""

    page_or_section: str
    """Human-readable locator: "page 2", "Section 4.1", "lab_result #41"."""

    field_or_chunk_id: str
    """Stable handle: extraction field key or retrieval chunk id."""

    quote_or_value: str
    """The literal extracted value or quoted text the claim is grounded in."""

    page_bbox: PageBBox | None = None
    """Required for LAB_PDF and INTAKE_FORM (validator enforces); None
    for GUIDELINE and OPENEMR_RECORD."""

    @model_validator(mode="after")
    def _scanned_sources_require_high_confidence_bbox(self) -> Citation:
        if self.source_type in (SourceType.LAB_PDF, SourceType.INTAKE_FORM):
            if self.page_bbox is None:
                raise ValueError(
                    f"Scanned-source citations ({self.source_type.value}) "
                    f"require page_bbox; received None."
                )
            if self.page_bbox.bbox_confidence < SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR:
                raise ValueError(
                    f"Scanned-source citations ({self.source_type.value}) "
                    f"require bbox_confidence >= "
                    f"{SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR}; "
                    f"received {self.page_bbox.bbox_confidence}. "
                    f"Lower-confidence fields belong in unsupported_fields."
                )
        return self
