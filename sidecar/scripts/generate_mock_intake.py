"""Generate a realistic-looking mock patient-intake-form PDF for the
W2 ``intake_extraction_demo.py`` (and any future demo / fixture work).

The output mimics the typical paper intake form a primary-care clinic
hands a patient at check-in: clinic letterhead, demographics block,
chief-concern free text, then sectioned lists (current medications,
allergies, family history). Rendered as a real form with field
labels and printed values — realistic enough that the vision tool
actually has to localize entries in tables rather than reading one
big text block at fixed coordinates.

Output is **deterministic**: ReportLab's ``invariant=1`` flag strips
the per-run trailer ID so re-running produces byte-identical PDFs.
The committed PDF is the demo fixture; the script is here so
contributors can regenerate it, see the values, or add fields.

The values are **synthetic and realistic but not real**. The patient
profile mirrors the mock lab PDF (T2DM + CKD + hyperlipidemia, female,
born 1972) so a future demo can extract both the lab and the intake
for the same synthetic patient and feed them through the agent loop
end-to-end. One known-illegible row in family history exercises the
``unsupported_fields`` path.

Run with:

    cd sidecar
    uv run python scripts/generate_mock_intake.py

This writes ``sidecar/data/samples/sample-intake.pdf``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class MedicationRow:
    name: str
    dose: str
    frequency: str


@dataclass(frozen=True)
class AllergyRow:
    substance: str
    reaction: str
    severity: str


@dataclass(frozen=True)
class FamilyRow:
    relative: str
    condition: str


# ---------------------------------------------------------------------------
# Realistic-but-synthetic intake content
# ---------------------------------------------------------------------------

# Same patient profile as sample-lab.pdf — T2DM + CKD + hyperlipidemia,
# female, DOB 1972-04-12. Lets a future demo run both extractions for
# the same synthetic identity.
_DEMOGRAPHICS: list[tuple[str, str]] = [
    ("Last Name", "Demo"),
    ("First Name", "Patient"),
    ("Date of Birth", "1972-04-12"),
    ("Sex", "Female"),
    ("Address", "100 Synthetic Ave, Anytown, USA 00000"),
    ("Phone", "(555) 123-4567"),
    ("Email", "demo.patient@example.invalid"),
    ("MRN", "DEMO-00042"),
]

_CHIEF_CONCERN = (
    "Follow-up for diabetes and high cholesterol; getting tired more "
    "easily over the last month."
)

_MEDICATIONS: list[MedicationRow] = [
    MedicationRow("Metformin", "500 mg", "twice daily"),
    MedicationRow("Atorvastatin", "40 mg", "once daily"),
    MedicationRow("Lisinopril", "10 mg", "once daily"),
    MedicationRow("Aspirin", "81 mg", "once daily"),
]

_ALLERGIES: list[AllergyRow] = [
    AllergyRow("Penicillin", "rash", "moderate"),
    AllergyRow("Sulfa drugs", "hives", "mild"),
]

_FAMILY_HISTORY: list[FamilyRow] = [
    FamilyRow("Mother", "Type 2 diabetes"),
    FamilyRow("Father", "Hypertension"),
    FamilyRow("Sister", "High cholesterol"),
    # Deliberately illegible row — the relative cell is blank. The
    # vision tool should drop this into unsupported_fields rather than
    # invent a relative. Exercises the worker's anti-invention rule.
    FamilyRow("", "Heart attack at 55"),
]


# ---------------------------------------------------------------------------
# PDF composition
# ---------------------------------------------------------------------------

def _header_paragraphs() -> list[Paragraph]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="ClinicTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=colors.HexColor("#0b3d62"),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        name="ClinicSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#0b3d62"),
        spaceAfter=4,
    )
    form_style = ParagraphStyle(
        name="FormTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=colors.black,
        spaceAfter=12,
    )
    return [
        Paragraph("DEMO PRIMARY CARE CLINIC", title_style),
        Paragraph(
            "200 Synthetic Boulevard, Anytown, USA 00000 | (555) 999-0000",
            subtitle_style,
        ),
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


def _demographics_table() -> Table:
    rows = []
    for i in range(0, len(_DEMOGRAPHICS), 2):
        left = _DEMOGRAPHICS[i]
        right = _DEMOGRAPHICS[i + 1] if i + 1 < len(_DEMOGRAPHICS) else ("", "")
        rows.append([f"{left[0]}:", left[1], f"{right[0]}:", right[1]])
    table = Table(
        rows,
        colWidths=[1.3 * inch, 2.0 * inch, 1.3 * inch, 2.0 * inch],
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (1, 0), (1, -1), 0.25, colors.lightgrey),
        ("LINEBELOW", (3, 0), (3, -1), 0.25, colors.lightgrey),
    ]))
    return table


def _chief_concern_paragraph() -> Paragraph:
    style = ParagraphStyle(
        name="ChiefConcern",
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.black,
        leading=14,
        leftIndent=8,
        borderPadding=6,
        backColor=colors.HexColor("#f6f9fc"),
        borderColor=colors.HexColor("#cccccc"),
        borderWidth=0.5,
        spaceAfter=8,
    )
    return Paragraph(_CHIEF_CONCERN, style)


def _medications_table() -> Table:
    body = [["Medication", "Dose", "Frequency"]]
    for m in _MEDICATIONS:
        body.append([m.name, m.dose, m.frequency])
    return _styled_form_table(body, col_widths=[2.4 * inch, 1.4 * inch, 2.0 * inch])


def _allergies_table() -> Table:
    body = [["Substance", "Reaction", "Severity"]]
    for a in _ALLERGIES:
        body.append([a.substance, a.reaction, a.severity])
    return _styled_form_table(body, col_widths=[2.0 * inch, 2.0 * inch, 1.5 * inch])


def _family_history_table() -> Table:
    body = [["Relative", "Condition / Diagnosis"]]
    for f in _FAMILY_HISTORY:
        body.append([f.relative, f.condition])
    return _styled_form_table(body, col_widths=[2.0 * inch, 4.5 * inch])


def _styled_form_table(body: list[list[str]], col_widths: list[float]) -> Table:
    table = Table(body, colWidths=col_widths, hAlign="LEFT")
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d62")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0b3d62")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fc")]),
        ("BOX", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ]
    return Table(body, colWidths=col_widths, hAlign="LEFT", style=TableStyle(style_cmds))


def _footer_paragraph() -> Paragraph:
    style = ParagraphStyle(
        name="Footer",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=colors.grey,
        spaceBefore=18,
    )
    return Paragraph(
        "** DEMO DATA — NOT A REAL PATIENT INTAKE FORM — VALUES ARE SYNTHETIC **<br/>"
        "Generated by AgentForge mock-intake generator. "
        "Not for clinical use. End of form.",
        style,
    )


def build_pdf(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # invariant=1 strips the per-run trailer ID so the bytes are
    # deterministic across runs.
    doc = BaseDocTemplate(
        str(output_path),
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

    # ReportLab story is a heterogeneous list of Flowable subclasses
    # (Paragraph, Table, Spacer, ...). The platypus types are untyped
    # so we widen to Any rather than bringing in a partial protocol.
    from typing import Any

    story: list[Any] = []
    story.extend(_header_paragraphs())

    story.append(_section_heading("Patient Information"))
    story.append(_demographics_table())
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Reason for Today's Visit (Chief Concern)"))
    story.append(_chief_concern_paragraph())

    story.append(_section_heading("Current Medications"))
    story.append(_medications_table())
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Allergies"))
    story.append(_allergies_table())
    story.append(Spacer(1, 0.05 * inch))

    story.append(_section_heading("Family Medical History"))
    story.append(_family_history_table())

    story.append(_footer_paragraph())

    doc.build(story)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "data"
            / "samples"
            / "sample-intake.pdf"
        ),
        help="Output path for the generated PDF.",
    )
    args = parser.parse_args(argv)

    build_pdf(args.output)
    size = args.output.stat().st_size
    print(f"Wrote {args.output} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
