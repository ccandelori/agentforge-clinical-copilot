"""Generate a realistic-looking mock lab-result PDF for the W2
``extraction_demo.py`` (and any future demo / fixture work).

The output mimics a Quest / LabCorp report shape — letterhead,
patient + provider header, accession number, then a tabular result
section with reference ranges and abnormal flags. Realistic enough
that the vision tool actually has to work to localize values rather
than hitting one big text block at fixed coordinates.

Output is **deterministic**: the script seeds Python's random with a
fixed value so re-running produces byte-identical PDFs (give or take
ReportLab's internal trailer ID, which is suppressed via
``invariant=1``). Re-running and committing the generated PDF won't
churn the diff every time someone runs the script.

The values are **synthetic and realistic but not real**. They mimic
the typical CMP / CBC / lipid / A1C panels a primary-care provider
would see, with one or two abnormals so the demo's extraction has
non-trivial flagged values to surface.

Run with:

    cd sidecar
    uv run python scripts/generate_mock_lab.py

This writes ``sidecar/data/samples/sample-lab.pdf``. The committed
PDF is the demo fixture; the script is here so contributors can
regenerate it, see the values, or add panels.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import LETTER  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
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
class LabRow:
    test_name: str
    loinc: str
    value: str
    units: str
    reference_range: str
    flag: str  # one of: '', 'H', 'L', 'HH', 'LL'


# ---------------------------------------------------------------------------
# Realistic-but-synthetic results
# ---------------------------------------------------------------------------

# A type-2-diabetic-with-mild-CKD-and-hyperlipidemia profile, picked
# to give the agent something to reason about. Two HIGH flags (A1C,
# LDL), one LOW flag (HDL), the rest normal — enough variety to
# exercise the abnormal_flag mapping without overwhelming the demo.
_PATIENT_INFO: dict[str, str] = {
    "Patient": "Demo, Patient",
    "DOB": "1972-04-12",
    "Sex": "F",
    "MRN": "DEMO-00042",
    "Account": "ACC-2026-04-15-001",
    "Collected": "2026-04-15 08:14",
    "Reported": "2026-04-15 14:32",
    "Ordering Provider": "Smith, J., MD",
    "Lab Director": "Jones, R., MD",
}

_CBC_ROWS: list[LabRow] = [
    LabRow("WBC", "6690-2", "7.2", "K/uL", "4.0-11.0", ""),
    LabRow("RBC", "789-8", "4.6", "M/uL", "4.0-5.5", ""),
    LabRow("Hemoglobin", "718-7", "13.1", "g/dL", "12.0-16.0", ""),
    LabRow("Hematocrit", "4544-3", "39.5", "%", "36-46", ""),
    LabRow("Platelets", "777-3", "245", "K/uL", "150-400", ""),
]

_CMP_ROWS: list[LabRow] = [
    LabRow("Sodium", "2951-2", "139", "mmol/L", "136-145", ""),
    LabRow("Potassium", "2823-3", "4.2", "mmol/L", "3.5-5.1", ""),
    LabRow("Chloride", "2075-0", "104", "mmol/L", "98-107", ""),
    LabRow("CO2", "2028-9", "24", "mmol/L", "22-29", ""),
    LabRow("BUN", "3094-0", "22", "mg/dL", "7-20", "H"),
    LabRow("Creatinine", "2160-0", "1.3", "mg/dL", "0.6-1.2", "H"),
    LabRow("eGFR (CKD-EPI 2021)", "62238-1", "55", "mL/min/1.73m2", "≥60", "L"),
    LabRow("Glucose", "2345-7", "182", "mg/dL", "70-99", "H"),
    LabRow("Calcium", "17861-6", "9.4", "mg/dL", "8.5-10.5", ""),
]

_LIPID_ROWS: list[LabRow] = [
    LabRow("Total Cholesterol", "2093-3", "224", "mg/dL", "<200", "H"),
    LabRow("HDL Cholesterol", "2085-9", "38", "mg/dL", ">40 (M), >50 (F)", "L"),
    LabRow("LDL Cholesterol (calc)", "13457-7", "152", "mg/dL", "<100", "H"),
    LabRow("Triglycerides", "2571-8", "168", "mg/dL", "<150", "H"),
]

_DIABETES_ROWS: list[LabRow] = [
    LabRow("Hemoglobin A1c", "4548-4", "9.2", "%", "<5.7 (normal)", "H"),
    LabRow("Estimated Avg Glucose", "27353-2", "217", "mg/dL", "—", ""),
]


# ---------------------------------------------------------------------------
# PDF composition
# ---------------------------------------------------------------------------

def _flag_color(flag: str) -> colors.Color:
    if flag in ("H", "HH"):
        return colors.HexColor("#c0392b")
    if flag in ("L", "LL"):
        return colors.HexColor("#1f6f8b")
    return colors.black


def _header_paragraphs() -> list[Paragraph]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="LabTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=colors.HexColor("#0b3d62"),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        name="LabSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#0b3d62"),
        spaceAfter=12,
    )
    return [
        Paragraph("DEMO REFERENCE LABORATORY", title_style),
        Paragraph(
            "100 Synthetic Avenue, Anytown, USA 00000 | CLIA: 99D9999999",
            subtitle_style,
        ),
    ]


def _patient_table() -> Table:
    rows = []
    items = list(_PATIENT_INFO.items())
    for i in range(0, len(items), 2):
        left = items[i]
        right = items[i + 1] if i + 1 < len(items) else ("", "")
        rows.append(
            [f"{left[0]}:", left[1], f"{right[0]}:", right[1]]
        )
    table = Table(
        rows,
        colWidths=[1.3 * inch, 2.0 * inch, 1.3 * inch, 2.0 * inch],
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _panel_table(panel_name: str, rows: list[LabRow]) -> Table:
    header = ["Test", "LOINC", "Result", "Units", "Reference Range", "Flag"]
    body = [header]
    for r in rows:
        body.append([
            r.test_name,
            r.loinc,
            r.value,
            r.units,
            r.reference_range,
            r.flag,
        ])
    table = Table(
        body,
        colWidths=[1.85 * inch, 0.7 * inch, 0.65 * inch, 0.85 * inch, 1.5 * inch, 0.45 * inch],
        hAlign="LEFT",
    )

    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d62")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),  # result column right-align
        ("ALIGN", (5, 1), (5, -1), "CENTER"),  # flag column
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0b3d62")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fc")]),
    ]
    for row_idx, row in enumerate(rows, start=1):
        if row.flag:
            color = _flag_color(row.flag)
            style_cmds.append(("TEXTCOLOR", (2, row_idx), (2, row_idx), color))
            style_cmds.append(("TEXTCOLOR", (5, row_idx), (5, row_idx), color))
            style_cmds.append(("FONTNAME", (5, row_idx), (5, row_idx), "Helvetica-Bold"))

    table.setStyle(TableStyle(style_cmds))
    return table


def _panel_heading(panel_name: str) -> Paragraph:
    style = ParagraphStyle(
        name="PanelHeading",
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=colors.HexColor("#0b3d62"),
        spaceBefore=10,
        spaceAfter=4,
    )
    return Paragraph(panel_name, style)


def _footer_paragraph() -> Paragraph:
    style = ParagraphStyle(
        name="Footer",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=colors.grey,
        spaceBefore=14,
    )
    return Paragraph(
        "** DEMO DATA — NOT A REAL LAB REPORT — VALUES ARE SYNTHETIC **<br/>"
        "Generated by AgentForge mock-lab generator. "
        "Not for clinical use. End of report.",
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
    story.append(_patient_table())
    story.append(Spacer(1, 0.1 * inch))

    for panel_name, rows in [
        ("Diabetes Monitoring", _DIABETES_ROWS),
        ("Comprehensive Metabolic Panel", _CMP_ROWS),
        ("Lipid Panel", _LIPID_ROWS),
        ("Complete Blood Count", _CBC_ROWS),
    ]:
        story.append(_panel_heading(panel_name))
        story.append(_panel_table(panel_name, rows))

    story.append(_footer_paragraph())

    doc.build(story)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "samples" / "sample-lab.pdf",
        help="Output path for the generated PDF.",
    )
    args = parser.parse_args(argv)

    build_pdf(args.output)
    size = args.output.stat().st_size
    print(f"Wrote {args.output} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
