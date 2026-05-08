"""Inline fixtures for the intake-form E2E happy-path test (Task 29).

The intake e2e test runs as a *process-level* integration: real BFF
upload route, real PdfRenderer + VisionExtractor wiring, mocked
Anthropic client, mocked PHP boundary via :class:`httpx.MockTransport`.
That keeps the test offline (no real Anthropic, no dev-easy stack, no
real DB) while still exercising the full upload → extract → persist
pipeline against the production code paths.

The companion lab e2e test (Task 28) lives in a parallel worktree.
We deliberately don't share fixtures via ``conftest.py`` to avoid
merge conflicts at the seam between the two tasks. The PDF builder
exposed here is intentionally parameterizable so Task 28 can adopt
it post-merge if it wants the reusable knob.

What this module provides:

* :func:`build_intake_pdf_bytes` — synthetic intake-form PDF generator,
  parameterizable on the five sections (demographics, chief concern,
  medications, allergies, family history). Emits deterministic bytes
  via ReportLab's ``invariant=1``.
* :data:`PINNED_INTAKE_CONTENT` — the canonical content the e2e test
  pins against. Each subtask asserts exact strings against this so a
  drift in either the generator or the extraction prompt surfaces
  loudly rather than as a silently-shifted fixture.
* :func:`canned_intake_extraction` — a hand-rolled
  ``IntakeFormExtraction``-shaped dict that the mocked Anthropic
  ``messages.create`` returns to the extractor. Built from
  :data:`PINNED_INTAKE_CONTENT` so the extraction matches the PDF.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import LETTER  # type: ignore[import-untyped]
from reportlab.lib.styles import (  # type: ignore[import-untyped]
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import inch  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Pinned content — the e2e test asserts exact strings against this so a
# drift in either the generator or the extraction prompt surfaces loudly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeContent:
    """Pinned intake-form content the e2e test asserts against.

    Used to (a) drive the generated PDF's body text and (b) seed the
    canned extraction the mocked Anthropic client returns. By pinning
    one source for both the source PDF and the canned answer, we keep
    the e2e flow self-consistent — a future contributor who edits one
    side without the other sees a test failure rather than a silent
    drift.
    """

    demographics: list[tuple[str, str]]
    chief_concern: str
    medications: list[tuple[str, str, str]]
    """Each entry: (name, dose, frequency)."""
    allergies: list[tuple[str, str, str]]
    """Each entry: (substance, reaction, severity)."""
    family_history: list[tuple[str, str]]
    """Each entry: (relative, condition)."""


PINNED_INTAKE_CONTENT: IntakeContent = IntakeContent(
    demographics=[
        ("Last Name", "Synthetic"),
        ("First Name", "Patient"),
        ("Date of Birth", "1972-04-12"),
        ("Sex", "Female"),
    ],
    chief_concern=(
        "Follow-up for diabetes; tired more easily over the last month."
    ),
    medications=[
        ("Metformin", "500 mg", "twice daily"),
        ("Atorvastatin", "40 mg", "once daily"),
    ],
    allergies=[
        ("Penicillin", "rash", "moderate"),
    ],
    family_history=[
        ("Mother", "Type 2 diabetes"),
        ("Father", "Hypertension"),
    ],
)


# ---------------------------------------------------------------------------
# PDF generator
# ---------------------------------------------------------------------------


def build_intake_pdf_bytes(content: IntakeContent = PINNED_INTAKE_CONTENT) -> bytes:
    """Build a synthetic intake-form PDF in memory.

    The PDF carries all five sections specified by Task 29 acceptance
    criteria: demographics, medications, allergies, family history, and
    chief concern. ``invariant=1`` strips the ReportLab trailer ID so
    the bytes are deterministic across runs (same content in =>
    same bytes out), which keeps the e2e test free of timestamp-based
    flakiness.

    Returns the raw PDF bytes — the upload route consumes a multipart
    body whose file part is bytes, so the e2e test feeds these straight
    in. No temp files, no on-disk fixture rotting.
    """
    buf = BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        invariant=1,
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="main",
    )
    doc.addPageTemplates(PageTemplate(id="default", frames=[frame]))

    story: list[Any] = []
    story.extend(_header_paragraphs())

    story.append(_section_heading("Patient Information"))
    story.append(_demographics_table(content.demographics))
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Reason for Today's Visit (Chief Concern)"))
    story.append(_chief_concern_paragraph(content.chief_concern))

    story.append(_section_heading("Current Medications"))
    story.append(_medications_table(content.medications))
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Allergies"))
    story.append(_allergies_table(content.allergies))
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Family Medical History"))
    story.append(_family_history_table(content.family_history))

    story.append(_footer_paragraph())

    doc.build(story)
    return buf.getvalue()


def _header_paragraphs() -> list[Paragraph]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="ClinicTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=16,
        spaceAfter=2,
    )
    form_style = ParagraphStyle(
        name="FormTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        spaceAfter=12,
    )
    return [
        Paragraph("E2E TEST INTAKE CLINIC", title_style),
        Paragraph("New Patient Intake Form", form_style),
    ]


def _section_heading(label: str) -> Paragraph:
    style = ParagraphStyle(
        name="SectionHeading",
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=colors.HexColor("#0b3d62"),
        spaceBefore=10,
        spaceAfter=4,
    )
    return Paragraph(label, style)


def _demographics_table(rows: list[tuple[str, str]]) -> Table:
    body = [[f"{label}:", value] for label, value in rows]
    table = Table(body, colWidths=[1.5 * inch, 4.5 * inch])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _chief_concern_paragraph(text: str) -> Paragraph:
    style = ParagraphStyle(
        name="ChiefConcern",
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        leftIndent=8,
        borderPadding=6,
        backColor=colors.HexColor("#f6f9fc"),
        borderColor=colors.HexColor("#cccccc"),
        borderWidth=0.5,
        spaceAfter=8,
    )
    return Paragraph(text, style)


def _medications_table(rows: list[tuple[str, str, str]]) -> Table:
    body = [["Medication", "Dose", "Frequency"]]
    body.extend([[name, dose, freq] for name, dose, freq in rows])
    return _styled_form_table(
        body, col_widths=[2.4 * inch, 1.4 * inch, 2.0 * inch]
    )


def _allergies_table(rows: list[tuple[str, str, str]]) -> Table:
    body = [["Substance", "Reaction", "Severity"]]
    body.extend([[sub, rxn, sev] for sub, rxn, sev in rows])
    return _styled_form_table(
        body, col_widths=[2.0 * inch, 2.0 * inch, 1.5 * inch]
    )


def _family_history_table(rows: list[tuple[str, str]]) -> Table:
    body = [["Relative", "Condition / Diagnosis"]]
    body.extend([[rel, cond] for rel, cond in rows])
    return _styled_form_table(body, col_widths=[2.0 * inch, 4.5 * inch])


def _styled_form_table(body: list[list[str]], col_widths: list[float]) -> Table:
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d62")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ]
    return Table(
        body,
        colWidths=col_widths,
        hAlign="LEFT",
        style=TableStyle(style_cmds),
    )


def _footer_paragraph() -> Paragraph:
    style = ParagraphStyle(
        name="Footer",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=colors.grey,
        spaceBefore=18,
    )
    return Paragraph("** E2E TEST DATA — NOT FOR CLINICAL USE **", style)


# ---------------------------------------------------------------------------
# Canned extraction — the shape the mocked Anthropic client returns.
# ---------------------------------------------------------------------------


def canned_intake_extraction(
    *,
    document_id: int,
    patient_id: int,
    content: IntakeContent = PINNED_INTAKE_CONTENT,
) -> dict[str, Any]:
    """Return the dict shape the mocked Anthropic ``messages.create``
    emits as the tool_use input for ``emit_intake_form_extraction``.

    The dict mirrors :class:`agentforge.schemas.intake.IntakeFormExtraction`'s
    wire shape with citations whose ``bbox_confidence`` clears the 0.7
    floor. Every claim is attributed: chief concern + each list entry.
    Built from the same :data:`PINNED_INTAKE_CONTENT` that drives the
    PDF, so the canned answer matches the source document.
    """
    return {
        "document_id": document_id,
        "patient_id": patient_id,
        "chief_concern": content.chief_concern,
        "chief_concern_citation": _bbox_citation(
            document_id=document_id,
            field_id="chief_concern",
            value=content.chief_concern,
            page_or_section="page 1, Reason for Visit",
            x0=0.05,
            y0=0.30,
            x1=0.95,
            y1=0.36,
        ),
        "demographics": [
            {
                "field": _normalize_field_key(label),
                "value": value,
                "citation": _bbox_citation(
                    document_id=document_id,
                    field_id=_normalize_field_key(label),
                    value=value,
                    page_or_section="page 1, Patient Information",
                    x0=0.05 + 0.01 * i,
                    y0=0.10 + 0.02 * i,
                    x1=0.45 + 0.01 * i,
                    y1=0.12 + 0.02 * i,
                ),
            }
            for i, (label, value) in enumerate(content.demographics)
        ],
        "medications": [
            {
                "name": name,
                "dose": dose,
                "frequency": freq,
                "citation": _bbox_citation(
                    document_id=document_id,
                    field_id=f"medications[{i}]",
                    value=name,
                    page_or_section="page 1, Current Medications",
                    x0=0.05,
                    y0=0.45 + 0.04 * i,
                    x1=0.55,
                    y1=0.47 + 0.04 * i,
                ),
            }
            for i, (name, dose, freq) in enumerate(content.medications)
        ],
        "allergies": [
            {
                "substance": sub,
                "reaction": rxn,
                "severity": sev,
                "citation": _bbox_citation(
                    document_id=document_id,
                    field_id=f"allergies[{i}]",
                    value=sub,
                    page_or_section="page 1, Allergies",
                    x0=0.05,
                    y0=0.60 + 0.04 * i,
                    x1=0.55,
                    y1=0.62 + 0.04 * i,
                ),
            }
            for i, (sub, rxn, sev) in enumerate(content.allergies)
        ],
        "family_history": [
            {
                "relative": rel,
                "condition": cond,
                "citation": _bbox_citation(
                    document_id=document_id,
                    field_id=f"family_history[{i}]",
                    value=f"{rel}: {cond}",
                    page_or_section="page 1, Family Medical History",
                    x0=0.05,
                    y0=0.75 + 0.03 * i,
                    x1=0.55,
                    y1=0.77 + 0.03 * i,
                ),
            }
            for i, (rel, cond) in enumerate(content.family_history)
        ],
        "extraction_confidence": 0.93,
        "unsupported_fields": [],
    }


def _normalize_field_key(label: str) -> str:
    """Map a PDF field label to a stable extraction key.

    Keeps the canned-extraction keys lowercase + underscored so the
    persistence layer can map them deterministically. The production
    extractor does the same shaping in the prompt.
    """
    return label.lower().replace(" ", "_")


def _bbox_citation(
    *,
    document_id: int,
    field_id: str,
    value: str,
    page_or_section: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    bbox_confidence: float = 0.92,
) -> dict[str, Any]:
    """Build one citation dict with a high-confidence bbox.

    bbox_confidence defaults to 0.92 so every entry clears the 0.7
    floor enforced by :class:`Citation`. The geometry is illustrative,
    not measured — the e2e test never renders the bbox; it only
    asserts the citation contract validates.
    """
    return {
        "source_type": "intake_form",
        "source_id": str(document_id),
        "page_or_section": page_or_section,
        "field_or_chunk_id": field_id,
        "quote_or_value": value,
        "page_bbox": {
            "page": 1,
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "bbox_confidence": bbox_confidence,
        },
    }
