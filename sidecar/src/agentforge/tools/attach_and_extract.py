"""W2 Task 11: ``attach_and_extract`` — vision-based extraction of
scanned lab PDFs into a structured ``LabPdfExtraction`` payload.

The tool's pipeline is:

  1. ``PdfRenderer.render_pages(pdf_bytes)`` — open in-memory bytes
     with PyMuPDF, render each page at 150 DPI to a PNG, base64-encode
     for the Anthropic Messages API. Bytes never touch disk.
  2. ``VisionExtractor.extract(images, document_id, patient_id)`` —
     POST the images plus a strict-schema prompt to Claude vision.
     The response is forced through a tool-use shape so the model
     emits structured JSON we can ``LabPdfExtraction.model_validate``
     directly. Each lab value's ``citation.page_bbox`` carries the
     model's stated bounding box (normalized 0..1 page coordinates).
  3. The caller persists the validated extraction by POSTing it to
     ``persist_lab_result.php`` (Task 8). The persistence step lives
     outside this module so the extraction stays unit-testable
     without spinning up Apache.

The intake-form variant (Task 13) extends ``VisionExtractor`` with a
different prompt + response schema (``IntakeFormExtraction``); the
renderer is identical.

Security constraints honored:

- **Bytes stay in memory.** ``fitz.open(stream=...)`` reads from a
  bytes buffer; we never write the PDF to a temp file. The base64
  PNG strings live on the request stack and are released when the
  call returns.
- **No PHI in logs.** This module never logs the raw extraction
  body — only structural metadata (page count, model used, token
  usage). PHI redaction at the Langfuse boundary is Task 14's
  surface; this module's contribution is to never produce a log
  line that contains extracted text.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

import fitz  # type: ignore[import-untyped]  # PyMuPDF; ships without py.typed marker
from anthropic import AsyncAnthropic
from anthropic.types import (
    Base64ImageSourceParam,
    ImageBlockParam,
    Message,
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolUseBlock,
)
from pydantic import ValidationError

from agentforge.schemas.lab import LabPdfExtraction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PdfRenderer
# ---------------------------------------------------------------------------

DEFAULT_DPI = 150  # legibility vs token cost; eval suite (Task 16+) may tune


class RenderedPage:
    """One page's PNG bytes with size metadata.

    Returned by ``PdfRenderer.render_pages``; the consumer base64-
    encodes ``png_bytes`` and embeds it as an Anthropic image block.
    Pixel width/height are useful for downstream coordinate-space
    sanity checks (e.g. confirming the model's normalized bbox falls
    within the actual rendered region).
    """

    __slots__ = ("page_number", "pixel_height", "pixel_width", "png_bytes")

    def __init__(
        self,
        *,
        page_number: int,
        png_bytes: bytes,
        pixel_width: int,
        pixel_height: int,
    ) -> None:
        self.page_number = page_number
        self.png_bytes = png_bytes
        self.pixel_width = pixel_width
        self.pixel_height = pixel_height


class PdfRenderer:
    """Renders PDF bytes to per-page PNG images using PyMuPDF.

    Stateless; reuse a single instance across calls. The ``dpi`` knob
    is an instance field so different callers (fast preview vs full
    extraction) can pick their own quality/cost tradeoff.
    """

    def __init__(self, dpi: int = DEFAULT_DPI) -> None:
        if dpi <= 0:
            raise ValueError(f"dpi must be positive; got {dpi}")
        self.dpi = dpi

    def render_pages(self, pdf_bytes: bytes) -> list[RenderedPage]:
        """Open ``pdf_bytes`` in memory and render every page to PNG.

        Raises ``ValueError`` if the bytes don't parse as a PDF (this
        catches the upstream case where the document is misclassified
        in OpenEMR — e.g. a JPEG stored with a .pdf extension).
        """
        if not pdf_bytes:
            raise ValueError("pdf_bytes is empty; cannot render")

        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"failed to open PDF bytes: {exc}") from exc

        pages: list[RenderedPage] = []
        try:
            for index, page in enumerate(document):
                pixmap = page.get_pixmap(dpi=self.dpi)
                pages.append(
                    RenderedPage(
                        page_number=index + 1,  # 1-indexed to match PageBBox
                        png_bytes=pixmap.tobytes("png"),
                        pixel_width=pixmap.width,
                        pixel_height=pixmap.height,
                    )
                )
        finally:
            document.close()

        return pages


# ---------------------------------------------------------------------------
# VisionExtractor
# ---------------------------------------------------------------------------

# Anthropic tool name used to coerce structured output. The model never
# "sees" this — it's the JSON-shape-as-tool pattern.
_EXTRACT_TOOL_NAME = "emit_lab_pdf_extraction"

# Default model, env-overridable so we can pin/upgrade without code changes.
DEFAULT_VISION_MODEL = "claude-sonnet-4-5-20250929"


def _build_tool_spec() -> ToolParam:
    """Tool spec that mirrors LabPdfExtraction's wire shape.

    Defining the schema inline (rather than reflecting it from the
    Pydantic class) keeps the prompt's structural contract pinned —
    a Pydantic field rename shouldn't silently reshape what the LLM
    emits without us noticing in tests.
    """
    return {
        "name": _EXTRACT_TOOL_NAME,
        "description": (
            "Emit the structured lab-PDF extraction. Call this exactly "
            "once with the values you can confidently extract from the "
            "PDF pages provided. Every lab value MUST carry a citation "
            "whose page_bbox refers to the rectangular region of the "
            "page where the value was read; bbox coordinates are "
            "normalized to [0, 1] (top-left origin) and bbox_confidence "
            "must be at or above 0.7 — values you cannot localize that "
            "confidently belong in unsupported_fields, NOT in values[]."
        ),
        "input_schema": {
            "type": "object",
            "required": [
                "document_id",
                "patient_id",
                "values",
                "extraction_confidence",
            ],
            "properties": {
                "document_id": {"type": "integer"},
                "patient_id": {"type": "integer"},
                "ordering_provider": {"type": ["string", "null"]},
                "accession_number": {"type": ["string", "null"]},
                "values": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["test_name", "value", "citation"],
                        "properties": {
                            "test_name": {"type": "string"},
                            "loinc_code": {"type": ["string", "null"]},
                            "value": {"type": "string"},
                            "unit": {"type": ["string", "null"]},
                            "reference_range": {"type": ["string", "null"]},
                            "collection_date": {"type": ["string", "null"]},
                            "abnormal_flag": {
                                "type": "string",
                                "enum": [
                                    "normal",
                                    "high",
                                    "low",
                                    "critical_high",
                                    "critical_low",
                                    "unknown",
                                ],
                            },
                            "citation": {
                                "type": "object",
                                "required": [
                                    "source_type",
                                    "source_id",
                                    "page_or_section",
                                    "field_or_chunk_id",
                                    "quote_or_value",
                                    "page_bbox",
                                ],
                                "properties": {
                                    "source_type": {
                                        "type": "string",
                                        "const": "lab_pdf",
                                    },
                                    "source_id": {"type": "string"},
                                    "page_or_section": {"type": "string"},
                                    "field_or_chunk_id": {"type": "string"},
                                    "quote_or_value": {"type": "string"},
                                    "page_bbox": {
                                        "type": "object",
                                        "required": [
                                            "page",
                                            "x0",
                                            "y0",
                                            "x1",
                                            "y1",
                                            "bbox_confidence",
                                        ],
                                        "properties": {
                                            "page": {"type": "integer", "minimum": 1},
                                            "x0": {"type": "number"},
                                            "y0": {"type": "number"},
                                            "x1": {"type": "number"},
                                            "y1": {"type": "number"},
                                            "bbox_confidence": {"type": "number"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "extraction_confidence": {"type": "number"},
                "unsupported_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    }


_SYSTEM_PROMPT = (
    "You are a medical-record extraction assistant. You are given the "
    "rendered pages of a scanned lab-result PDF. Extract every lab value "
    "you can ground in the document into a structured emission via the "
    "emit_lab_pdf_extraction tool.\n\n"
    "Strict rules:\n"
    " - Only emit values whose bounding box you can identify with "
    "bbox_confidence >= 0.7. Lower-confidence reads belong in "
    "unsupported_fields, not values[].\n"
    " - source_type is always 'lab_pdf'. source_id is the document_id "
    "you are given. quote_or_value is the literal string from the PDF.\n"
    " - page_bbox.page is 1-indexed, matching the page sequence I send "
    "you. x0/y0/x1/y1 are normalized to [0, 1] with origin at top-left, "
    "and you MUST keep x1 > x0 and y1 > y0 (no inverted boxes).\n"
    " - If the patient name on the document does not match patient_id "
    "context (you cannot verify here), STILL extract; the persistence "
    "endpoint enforces the patient-scope check upstream.\n"
    " - Do not invent. If you cannot read a field, list its name in "
    "unsupported_fields and move on.\n"
    " - Call the tool exactly once. Do not emit any free text."
)


@dataclass(frozen=True)
class VisionExtractionResult:
    """The validated extraction plus the raw model metadata.

    ``input_tokens`` and ``output_tokens`` come from the Anthropic
    response; production observability (Task 14) will route these to
    Langfuse alongside a redacted prompt summary. We surface them
    here so the caller can log without re-parsing the SDK response.
    """

    extraction: LabPdfExtraction
    model: str
    input_tokens: int
    output_tokens: int


class VisionExtractor:
    """Drives Claude vision, validates the response as
    :class:`LabPdfExtraction`.

    The Anthropic client is injectable so tests can pass an
    ``AsyncMock`` whose ``messages.create`` returns a stub response
    without any network traffic.
    """

    def __init__(
        self,
        client: AsyncAnthropic | None = None,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._client = client or AsyncAnthropic()
        self._model = model or os.environ.get(
            "ANTHROPIC_VISION_MODEL", DEFAULT_VISION_MODEL
        )
        self._max_tokens = max_tokens

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
    ) -> VisionExtractionResult:
        if not pages:
            raise ValueError("pages list is empty; nothing to extract from")

        content_blocks: list[ImageBlockParam | TextBlockParam] = [
            ImageBlockParam(
                type="image",
                source=Base64ImageSourceParam(
                    type="base64",
                    media_type="image/png",
                    data=base64.b64encode(page.png_bytes).decode("ascii"),
                ),
            )
            for page in pages
        ]
        content_blocks.append(
            TextBlockParam(
                type="text",
                text=(
                    f"document_id = {document_id}\n"
                    f"patient_id = {patient_id}\n"
                    f"pages = {len(pages)}\n\n"
                    "Extract the lab values from these pages and emit "
                    "the structured result via emit_lab_pdf_extraction."
                ),
            )
        )

        messages: list[MessageParam] = [
            MessageParam(role="user", content=content_blocks),
        ]

        response: Message = await self._client.messages.create(
            model=self._model,
            system=_SYSTEM_PROMPT,
            messages=messages,
            tools=[_build_tool_spec()],
            tool_choice={"type": "tool", "name": _EXTRACT_TOOL_NAME},
            max_tokens=self._max_tokens,
        )

        tool_use = self._first_tool_use(response)
        if tool_use is None:
            raise RuntimeError(
                "vision response did not contain a tool_use block; "
                "model may have refused to emit structured output"
            )

        # Anthropic returns tool_use.input as already-parsed dict.
        try:
            extraction = LabPdfExtraction.model_validate(tool_use.input)
        except ValidationError as exc:
            # Don't include the payload itself in the log — that's PHI.
            logger.warning(
                "vision extraction failed schema validation",
                extra={
                    "model": self._model,
                    "page_count": len(pages),
                    "error_count": len(exc.errors()),
                },
            )
            raise

        usage = response.usage
        return VisionExtractionResult(
            extraction=extraction,
            model=self._model,
            input_tokens=int(usage.input_tokens),
            output_tokens=int(usage.output_tokens),
        )

    @staticmethod
    def _first_tool_use(response: Message) -> ToolUseBlock | None:
        for block in response.content:
            if isinstance(block, ToolUseBlock) and block.name == _EXTRACT_TOOL_NAME:
                return block
        return None
