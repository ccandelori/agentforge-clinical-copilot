"""W2 Tasks 11 + 13: ``attach_and_extract`` — vision-based extraction of
scanned PDFs (lab results, intake forms) into structured Pydantic
payloads.

The tool's pipeline is:

  1. ``PdfRenderer.render_pages(pdf_bytes)`` — open in-memory bytes
     with PyMuPDF, render each page at 150 DPI to a PNG, base64-encode
     for the Anthropic Messages API. Bytes never touch disk.
  2. ``VisionExtractor.extract(images, document_id, patient_id)`` —
     POST the images plus a strict-schema prompt to Claude vision.
     The response is forced through a tool-use shape so the model
     emits structured JSON we can validate against the contract's
     Pydantic class directly. Each value's ``citation.page_bbox``
     carries the model's stated bounding box (normalized 0..1 page
     coordinates).
  3. The caller persists the validated extraction by POSTing it to
     the appropriate endpoint (``persist_lab_result.php`` for labs,
     ``persist_questionnaire_response.php`` for intake forms). The
     persistence step lives outside this module so the extraction
     stays unit-testable without spinning up Apache.

The lab vs intake variants share the renderer and the extractor body;
they differ only in the prompt + tool spec + Pydantic schema. Those
three differences are bundled into a :class:`VisionContract`, and
:class:`VisionExtractor` is parameterized over the contract. The
module exposes :data:`LAB_CONTRACT` and :data:`INTAKE_CONTRACT`
constants for the two W2 doc types.

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
import time
from dataclasses import dataclass
from typing import Literal

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
from pydantic import BaseModel, ValidationError

from agentforge.observability.cost import calculate_cost
from agentforge.observability.protocols import LangfuseClient, TraceHandle
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.schemas.lab import LabPdfExtraction

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> int:
    """Convert a ``time.perf_counter()`` start to elapsed milliseconds.

    Mirrors the helper in :mod:`agentforge.orchestrator.__init__` so the
    Langfuse latency-ms convention is consistent across the codebase.
    """
    return int((time.perf_counter() - start) * 1000)


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
# VisionContract — bundles the per-doc-type configuration
# ---------------------------------------------------------------------------

# Default model, env-overridable so we can pin/upgrade without code changes.
DEFAULT_VISION_MODEL = "claude-sonnet-4-5-20250929"


@dataclass(frozen=True)
class VisionContract[T: BaseModel]:
    """Per-document-type configuration for :class:`VisionExtractor`.

    Bundles the four pieces that differ between vision flows:

    - ``tool_name``: the Anthropic tool the model is forced to call
      (via ``tool_choice``). Distinct names per flow keep the
      ``_first_tool_use`` filter unambiguous if the contracts ever
      coexist in one request.
    - ``tool_spec``: the JSON schema mirroring the wire shape of
      ``T``. Defined inline (not reflected from the Pydantic class)
      so a field rename can't silently reshape the LLM emission
      contract without a test failure.
    - ``system_prompt``: extraction-task instructions, including the
      anti-invention rules and the bbox-confidence floor reminder.
    - ``extraction_class``: the Pydantic model used to validate the
      model's tool_use payload.

    Frozen so contracts can be shared as module-level constants
    without risk of mutation. Generic over ``T`` so the extractor's
    return type binds to the contract's schema class.
    """

    tool_name: str
    tool_spec: ToolParam
    system_prompt: str
    extraction_class: type[T]


def _build_lab_tool_spec() -> ToolParam:
    """Tool spec that mirrors :class:`LabPdfExtraction`'s wire shape."""
    return {
        "name": "emit_lab_pdf_extraction",
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
                            "citation": _citation_schema(source_type="lab_pdf"),
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


def _build_intake_tool_spec() -> ToolParam:
    """Tool spec that mirrors :class:`IntakeFormExtraction`'s wire shape.

    The four list sections (demographics, medications, allergies,
    family_history) line up 1:1 with the canonical FHIR Questionnaire
    seeded by Task 5. ``chief_concern`` and ``chief_concern_citation``
    are both optional (a blank intake form is valid; a citation-less
    chief concern is the "extracted but bbox confidence below floor"
    shape — schema doesn't pair them, the worker's caller does).
    """
    citation_schema = _citation_schema(source_type="intake_form")
    return {
        "name": "emit_intake_form_extraction",
        "description": (
            "Emit the structured intake-form extraction. Call this "
            "exactly once with the demographics, medications, "
            "allergies, family-history entries, and chief concern you "
            "can confidently extract from the PDF pages provided. Every "
            "structured entry MUST carry a citation whose page_bbox "
            "refers to the rectangular region of the page where the "
            "value was read; bbox coordinates are normalized to [0, 1] "
            "(top-left origin) and bbox_confidence must be at or above "
            "0.7 — entries you cannot localize that confidently belong "
            "in unsupported_fields, NOT in the structured lists."
        ),
        "input_schema": {
            "type": "object",
            "required": [
                "document_id",
                "patient_id",
                "extraction_confidence",
            ],
            "properties": {
                "document_id": {"type": "integer"},
                "patient_id": {"type": "integer"},
                "chief_concern": {"type": ["string", "null"]},
                "chief_concern_citation": {
                    "anyOf": [citation_schema, {"type": "null"}],
                },
                "demographics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["field", "value", "citation"],
                        "properties": {
                            "field": {"type": "string"},
                            "value": {"type": "string"},
                            "citation": citation_schema,
                        },
                    },
                },
                "medications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "citation"],
                        "properties": {
                            "name": {"type": "string"},
                            "dose": {"type": ["string", "null"]},
                            "frequency": {"type": ["string", "null"]},
                            "citation": citation_schema,
                        },
                    },
                },
                "allergies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["substance", "citation"],
                        "properties": {
                            "substance": {"type": "string"},
                            "reaction": {"type": ["string", "null"]},
                            "severity": {"type": ["string", "null"]},
                            "citation": citation_schema,
                        },
                    },
                },
                "family_history": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["relative", "condition", "citation"],
                        "properties": {
                            "relative": {"type": "string"},
                            "condition": {"type": "string"},
                            "citation": citation_schema,
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


def _citation_schema(*, source_type: str) -> dict[str, object]:
    """Inline citation sub-schema. Shared between lab and intake tool
    specs since the citation contract is uniform across scanned-source
    extractions; ``source_type`` is the only per-flow constant."""
    return {
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
            "source_type": {"type": "string", "const": source_type},
            "source_id": {"type": "string"},
            "page_or_section": {"type": "string"},
            "field_or_chunk_id": {"type": "string"},
            "quote_or_value": {"type": "string"},
            "page_bbox": {
                "type": "object",
                "required": ["page", "x0", "y0", "x1", "y1", "bbox_confidence"],
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
    }


_LAB_SYSTEM_PROMPT = (
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


_INTAKE_SYSTEM_PROMPT = (
    "You are a medical-record extraction assistant. You are given the "
    "rendered pages of a scanned patient intake form. Extract the "
    "demographics, current medications, allergies, family history, and "
    "chief concern (reason for visit) you can ground in the document "
    "into a structured emission via the emit_intake_form_extraction "
    "tool.\n\n"
    "Strict rules:\n"
    " - Only emit structured entries whose bounding box you can "
    "identify with bbox_confidence >= 0.7. Lower-confidence reads "
    "belong in unsupported_fields, not in the structured lists.\n"
    " - source_type is always 'intake_form'. source_id is the "
    "document_id you are given. quote_or_value is the literal string "
    "from the form.\n"
    " - page_bbox.page is 1-indexed, matching the page sequence I send "
    "you. x0/y0/x1/y1 are normalized to [0, 1] with origin at top-left, "
    "and you MUST keep x1 > x0 and y1 > y0 (no inverted boxes).\n"
    " - Demographics: emit free-form (field, value) pairs. Common keys "
    "include date_of_birth, sex, address, phone, email — but use "
    "whatever the form labels. The persistence layer normalizes to FHIR "
    "codings.\n"
    " - Medications without a written dose/frequency are common; emit "
    "with dose=null and frequency=null rather than inventing values. "
    "Same for allergies missing reaction/severity.\n"
    " - Family-history entries require BOTH relative and condition. "
    "Cases where one is missing or illegible (e.g. 'Mother: ?') belong "
    "in unsupported_fields.\n"
    " - chief_concern is the patient's stated reason for visit, written "
    "in their words. If absent or unreadable, omit both chief_concern "
    "and chief_concern_citation.\n"
    " - Do not invent. If you cannot read a field, list its name in "
    "unsupported_fields and move on.\n"
    " - Call the tool exactly once. Do not emit any free text."
)


LAB_CONTRACT: VisionContract[LabPdfExtraction] = VisionContract(
    tool_name="emit_lab_pdf_extraction",
    tool_spec=_build_lab_tool_spec(),
    system_prompt=_LAB_SYSTEM_PROMPT,
    extraction_class=LabPdfExtraction,
)

INTAKE_CONTRACT: VisionContract[IntakeFormExtraction] = VisionContract(
    tool_name="emit_intake_form_extraction",
    tool_spec=_build_intake_tool_spec(),
    system_prompt=_INTAKE_SYSTEM_PROMPT,
    extraction_class=IntakeFormExtraction,
)


# ---------------------------------------------------------------------------
# VisionExtractor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisionExtractionResult[T: BaseModel]:
    """The validated extraction plus the raw model metadata.

    ``input_tokens`` and ``output_tokens`` come from the Anthropic
    response; production observability (Task 14) routes these to
    Langfuse alongside a redacted prompt summary. We surface them
    here so the caller can log without re-parsing the SDK response.

    ``cost_usd`` is the closed-form vision-call cost computed via
    :func:`agentforge.observability.cost.calculate_cost` against the
    Anthropic-reported tokens. Anthropic's billing already converts
    image pixel area into the input-token count it returns, so we use
    the standard text-rate path on the reported total — no double-
    counting. ``None`` when the model isn't in the pricing table
    (mirrors :func:`calculate_cost`'s soft-fail contract).
    """

    extraction: T
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class VisionExtractor[T: BaseModel]:
    """Drives Claude vision, validates the response against the
    contract's Pydantic class.

    Generic over the contract's schema type ``T`` so the return type
    of :meth:`extract` binds tightly — call sites operate on the
    concrete extraction (``LabPdfExtraction`` or
    ``IntakeFormExtraction``) without runtime casts.

    The Anthropic client is injectable so tests can pass an
    ``AsyncMock`` whose ``messages.create`` returns a stub response
    without any network traffic.
    """

    def __init__(
        self,
        *,
        contract: VisionContract[T],
        client: AsyncAnthropic | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        langfuse: LangfuseClient | None = None,
    ) -> None:
        self._contract = contract
        self._client = client or AsyncAnthropic()
        self._model = model or os.environ.get(
            "ANTHROPIC_VISION_MODEL", DEFAULT_VISION_MODEL
        )
        self._max_tokens = max_tokens
        # Optional Langfuse client. When wired alongside a per-turn
        # ``trace`` handle on :meth:`extract`, every call emits one
        # ``record_extraction_call`` span with redacted metadata
        # (model, page count, tokens, cost, schema_validation result).
        # The telemetry call itself is best-effort — any exception is
        # swallowed at the call site so observability never breaks
        # the extraction pipeline.
        self._langfuse = langfuse

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
        trace: TraceHandle | None = None,
    ) -> VisionExtractionResult[T]:
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
                    f"Extract from these pages and emit the structured "
                    f"result via {self._contract.tool_name}."
                ),
            )
        )

        messages: list[MessageParam] = [
            MessageParam(role="user", content=content_blocks),
        ]

        # Track wall-clock latency for the Langfuse extraction span so
        # dashboards can split slow extractions from fast ones without
        # parsing the SDK's response timestamps.
        call_start = time.perf_counter()
        response: Message = await self._client.messages.create(
            model=self._model,
            system=self._contract.system_prompt,
            messages=messages,
            tools=[self._contract.tool_spec],
            tool_choice={"type": "tool", "name": self._contract.tool_name},
            max_tokens=self._max_tokens,
        )

        tool_use = self._first_tool_use(response)
        if tool_use is None:
            raise RuntimeError(
                "vision response did not contain a tool_use block; "
                "model may have refused to emit structured output"
            )

        usage = response.usage
        input_tokens = int(usage.input_tokens)
        output_tokens = int(usage.output_tokens)
        # Anthropic's reported input_tokens already incorporates the
        # vision pricing rule (image pixel area → tokens), so the same
        # closed-form ``calculate_cost`` works for vision calls without
        # double-counting. ``calculate_vision_cost`` is reserved for
        # pre-call projections from page dimensions where we don't yet
        # have an Anthropic Usage object.
        cost = calculate_cost(self._model, input_tokens, output_tokens)
        cost_usd: float | None = cost if cost > 0.0 else None

        # Anthropic returns tool_use.input as already-parsed dict.
        try:
            extraction = self._contract.extraction_class.model_validate(tool_use.input)
        except ValidationError as exc:
            # Don't include the payload itself in the log — that's PHI.
            logger.warning(
                "vision extraction failed schema validation",
                extra={
                    "model": self._model,
                    "tool_name": self._contract.tool_name,
                    "page_count": len(pages),
                    "error_count": len(exc.errors()),
                },
            )
            # Emit a failure-shaped span before re-raising so failed
            # extractions show up on Langfuse dashboards alongside
            # successes. extraction_confidence is None — validation
            # failed before any confidence number was meaningful — and
            # unsupported_fields_count is 0 because we never reached a
            # validated extraction to read it from. No payload echo;
            # only the parameters the PHI-safe API accepts.
            self._maybe_record_extraction_call(
                trace=trace,
                schema_validation="fail",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=_elapsed_ms(call_start),
                page_count=len(pages),
                unsupported_fields_count=0,
                extraction_confidence=None,
                cost_usd=cost_usd,
            )
            raise

        # Successful extraction — pull the worker's self-rated
        # confidence and unsupported_fields count off the validated
        # model so dashboards can see both the structural counts and
        # the worker's self-assessment in the same span.
        extraction_confidence: float | None = getattr(
            extraction, "extraction_confidence", None
        )
        unsupported_fields = getattr(extraction, "unsupported_fields", None) or []
        self._maybe_record_extraction_call(
            trace=trace,
            schema_validation="pass",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=_elapsed_ms(call_start),
            page_count=len(pages),
            unsupported_fields_count=len(unsupported_fields),
            extraction_confidence=extraction_confidence,
            cost_usd=cost_usd,
        )

        return VisionExtractionResult(
            extraction=extraction,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def _maybe_record_extraction_call(
        self,
        *,
        trace: TraceHandle | None,
        schema_validation: Literal["pass", "fail"],
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        page_count: int,
        unsupported_fields_count: int,
        extraction_confidence: float | None,
        cost_usd: float | None,
    ) -> None:
        """Best-effort wrapper around :meth:`LangfuseClient.record_extraction_call`.

        Two short-circuits keep the no-op path zero-cost:

        1. ``self._langfuse is None`` — telemetry isn't wired (local-dev
           or unit-test default).
        2. ``trace is None`` — no per-turn handle (e.g. an extraction
           outside the supervisor turn loop). Mirrors the null-handle
           guard the other observability sites use.

        Any exception from the SDK is swallowed and logged at debug
        level so a Langfuse outage never fails a real extraction. We
        catch :class:`Exception` rather than :class:`BaseException` so
        :class:`KeyboardInterrupt` and :class:`SystemExit` still
        propagate during local debugging.
        """
        if self._langfuse is None or trace is None:
            return
        try:
            self._langfuse.record_extraction_call(
                trace,
                model=self._model,
                tool_name=self._contract.tool_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                schema_validation=schema_validation,
                page_count=page_count,
                unsupported_fields_count=unsupported_fields_count,
                extraction_confidence=extraction_confidence,
                cost_usd=cost_usd,
            )
        except Exception:
            # Telemetry is best-effort. A Langfuse SDK exception MUST
            # NOT fail the extraction. ``exc_info=True`` lets the debug
            # log include the traceback for triage without leaking PHI:
            # the surrounding extra={} carries only structural metadata.
            logger.debug(
                "record_extraction_call swallowed telemetry exception",
                extra={
                    "tool_name": self._contract.tool_name,
                    "schema_validation": schema_validation,
                    "page_count": page_count,
                },
                exc_info=True,
            )

    def _first_tool_use(self, response: Message) -> ToolUseBlock | None:
        for block in response.content:
            if (
                isinstance(block, ToolUseBlock)
                and block.name == self._contract.tool_name
            ):
                return block
        return None
