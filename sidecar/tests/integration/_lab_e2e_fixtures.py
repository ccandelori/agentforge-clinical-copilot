"""Local fixtures + helpers for the lab-PDF E2E integration test (Task 28).

Kept out of ``conftest.py`` deliberately — the live-stack ``conftest.py``
already gates the rest of ``tests/integration/`` on a reachable OpenEMR.
This test is process-level (mocked LLM, mocked PHP boundary) and must
stay runnable without docker/dev-easy. Sister Task 29 owns its own
intake-form fixtures by the same convention.

The helpers here are the test's "test scaffolding": a synthetic lab-PDF
generator, a canned-extraction builder, and the Protocol surfaces the
test composes against the production schemas + tools without modifying
either.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Protocol

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
# Synthetic lab-PDF generator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabRowSpec:
    """One analyte row in the synthetic lab PDF.

    Pinned values let the test assert on exact strings post-extraction.
    ``flag`` mirrors the printed-on-paper flag the lab would emit ('H',
    'L', '' for normal); the test mock translates these into the
    canonical ``AbnormalFlag`` enum.
    """

    test_name: str
    loinc: str
    value: str
    units: str
    reference_range: str
    flag: str  # one of: '', 'H', 'L'


# Default panels — pinned. The brief asks for "CBC normal + CMP w/ one
# flagged value + A1c". The CMP flag is BUN (slightly elevated, "H");
# A1c is also high to mimic a poorly controlled diabetic. CBC is fully
# normal so the test can assert "panel produced N rows, none flagged".
DEFAULT_CBC_NORMAL: tuple[LabRowSpec, ...] = (
    LabRowSpec("WBC", "6690-2", "7.2", "K/uL", "4.0-11.0", ""),
    LabRowSpec("RBC", "789-8", "4.6", "M/uL", "4.0-5.5", ""),
    LabRowSpec("Hemoglobin", "718-7", "13.1", "g/dL", "12.0-16.0", ""),
    LabRowSpec("Hematocrit", "4544-3", "39.5", "%", "36-46", ""),
    LabRowSpec("Platelets", "777-3", "245", "K/uL", "150-400", ""),
)

DEFAULT_CMP_ONE_FLAGGED: tuple[LabRowSpec, ...] = (
    LabRowSpec("Sodium", "2951-2", "139", "mmol/L", "136-145", ""),
    LabRowSpec("Potassium", "2823-3", "4.2", "mmol/L", "3.5-5.1", ""),
    LabRowSpec("Chloride", "2075-0", "104", "mmol/L", "98-107", ""),
    LabRowSpec("CO2", "2028-9", "24", "mmol/L", "22-29", ""),
    LabRowSpec("BUN", "3094-0", "22", "mg/dL", "7-20", "H"),
    LabRowSpec("Creatinine", "2160-0", "0.9", "mg/dL", "0.6-1.2", ""),
    LabRowSpec("Glucose", "2345-7", "94", "mg/dL", "70-99", ""),
    LabRowSpec("Calcium", "17861-6", "9.4", "mg/dL", "8.5-10.5", ""),
)

DEFAULT_A1C: LabRowSpec = LabRowSpec(
    "Hemoglobin A1c", "4548-4", "9.2", "%", "<5.7 (normal)", "H"
)


def _flag_color(flag: str) -> colors.Color:
    if flag == "H":
        return colors.HexColor("#c0392b")
    if flag == "L":
        return colors.HexColor("#1f6f8b")
    return colors.black


def _panel_table(rows: tuple[LabRowSpec, ...]) -> Table:
    header = ["Test", "LOINC", "Result", "Units", "Reference Range", "Flag"]
    body: list[list[str]] = [header]
    for r in rows:
        body.append(
            [r.test_name, r.loinc, r.value, r.units, r.reference_range, r.flag]
        )
    table = Table(
        body,
        colWidths=[
            1.85 * inch,
            0.7 * inch,
            0.65 * inch,
            0.85 * inch,
            1.5 * inch,
            0.45 * inch,
        ],
        hAlign="LEFT",
    )
    style_cmds: list[Any] = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d62")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ("ALIGN", (5, 1), (5, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_idx, row in enumerate(rows, start=1):
        if row.flag:
            color = _flag_color(row.flag)
            style_cmds.append(
                ("TEXTCOLOR", (2, row_idx), (2, row_idx), color)
            )
            style_cmds.append(
                ("TEXTCOLOR", (5, row_idx), (5, row_idx), color)
            )
            style_cmds.append(
                ("FONTNAME", (5, row_idx), (5, row_idx), "Helvetica-Bold")
            )
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


def build_lab_pdf_bytes(
    *,
    cbc_rows: tuple[LabRowSpec, ...] = DEFAULT_CBC_NORMAL,
    cmp_rows: tuple[LabRowSpec, ...] = DEFAULT_CMP_ONE_FLAGGED,
    a1c_row: LabRowSpec = DEFAULT_A1C,
    patient_name: str = "Test, Patient",
    accession: str = "ACC-2026-05-08-T28",
) -> bytes:
    """Compose a lab-PDF in memory and return its bytes.

    Parameterized over the three panels so other tests (or sister
    fixtures) can pin different values. The default panel set matches
    the Task 28 brief: CBC normal + CMP with one flagged value (BUN) +
    A1c. Output is a real ReportLab PDF — not just bytes with a PDF
    magic — so the round-trip through PyMuPDF rendering is genuine.
    """
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        invariant=1,
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main"
    )
    doc.addPageTemplates(PageTemplate(id="default", frames=[frame]))

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
        spaceAfter=8,
    )
    meta_style = ParagraphStyle(
        name="Meta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        spaceAfter=8,
    )

    story: list[Any] = [
        Paragraph("TEST REFERENCE LABORATORY (Synthetic)", title_style),
        Paragraph(
            "100 Synthetic Avenue, Anytown, USA 00000 | CLIA: 99D9999999",
            subtitle_style,
        ),
        Paragraph(
            f"<b>Patient:</b> {patient_name} &nbsp; "
            f"<b>Accession:</b> {accession} &nbsp; "
            f"<b>Collected:</b> 2026-05-08",
            meta_style,
        ),
        Spacer(1, 0.05 * inch),
        _panel_heading("Diabetes Monitoring"),
        _panel_table((a1c_row,)),
        _panel_heading("Comprehensive Metabolic Panel"),
        _panel_table(cmp_rows),
        _panel_heading("Complete Blood Count"),
        _panel_table(cbc_rows),
    ]

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Canned LabPdfExtraction builder
# ---------------------------------------------------------------------------


def lab_extraction_payload(
    *,
    document_id: int,
    patient_id: int,
    cbc_rows: tuple[LabRowSpec, ...] = DEFAULT_CBC_NORMAL,
    cmp_rows: tuple[LabRowSpec, ...] = DEFAULT_CMP_ONE_FLAGGED,
    a1c_row: LabRowSpec = DEFAULT_A1C,
    accession: str = "ACC-2026-05-08-T28",
) -> dict[str, Any]:
    """Build the dict the LLM tool_use would emit for the synthetic PDF.

    Mirrors :class:`LabPdfExtraction`'s wire shape exactly so the
    extraction phase can validate via ``model_validate(...)`` on the
    real schema. Citations reuse the synthesized PDF's bbox structure
    with ``bbox_confidence=0.9`` (above the 0.7 floor enforced by the
    system prompt). All values use the ``LOINC`` codes baked into the
    synthetic PDF so a downstream test that re-parses both can match
    PDF-truth ↔ extracted-truth.
    """

    def _value_block(row: LabRowSpec, page: int, y0: float) -> dict[str, Any]:
        flag_to_enum = {"": "normal", "H": "high", "L": "low"}
        return {
            "test_name": row.test_name,
            "loinc_code": row.loinc,
            "value": row.value,
            "unit": row.units,
            "reference_range": row.reference_range,
            "collection_date": "2026-05-08",
            "abnormal_flag": flag_to_enum[row.flag],
            "citation": {
                "source_type": "lab_pdf",
                "source_id": str(document_id),
                "page_or_section": f"page {page}",
                "field_or_chunk_id": f"{row.test_name.lower()}_value",
                "quote_or_value": f"{row.value} {row.units}",
                "page_bbox": {
                    "page": page,
                    "x0": 0.1,
                    "y0": y0,
                    "x1": 0.5,
                    "y1": y0 + 0.05,
                    "bbox_confidence": 0.9,
                },
            },
        }

    values: list[dict[str, Any]] = []
    y = 0.20
    values.append(_value_block(a1c_row, page=1, y0=y))
    y += 0.07
    for row in cmp_rows:
        values.append(_value_block(row, page=1, y0=y))
        y += 0.04
    for row in cbc_rows:
        values.append(_value_block(row, page=1, y0=y))
        y += 0.04

    return {
        "document_id": document_id,
        "patient_id": patient_id,
        "ordering_provider": "Dr. Test",
        "accession_number": accession,
        "values": values,
        "extraction_confidence": 0.92,
        "unsupported_fields": [],
    }


# ---------------------------------------------------------------------------
# Boundary protocols + capturing fakes — composes against the real
# upload writer without forcing a network call into PHP.
# ---------------------------------------------------------------------------


class LabPersistWriter(Protocol):
    """Boundary the test asserts against for the persist phase.

    The Python side does not yet have a production
    ``persist_lab_result.php`` client — the existing PHP controller
    (:class:`InternalLabPersistController`) is the canonical writer.
    Defining the Protocol locally lets the test exercise the contract
    we'd expect a future Python adapter to satisfy: take a validated
    extraction, return the freshly-created procedure_result IDs.

    Mirrors the PHP controller's response shape: order id, report id,
    list of per-analyte procedure_result ids. ``document_id`` is
    forwarded so the test can assert it lands on every result row.
    """

    async def persist(  # pragma: no cover — protocol stub
        self,
        *,
        extraction: Any,
    ) -> "PersistResult": ...


@dataclass(frozen=True)
class PersistResult:
    """Mirror of ``InternalLabPersistController``'s success body.

    ``procedure_result_ids`` is one id per analyte in the same order as
    ``extraction.values`` so the test can map back to LOINC and
    abnormal_flag. ``document_id`` is echoed to make the
    "result.document_id == upload.document_id" invariant testable
    without re-reading the request payload.
    """

    procedure_order_id: int
    procedure_report_id: int
    procedure_result_ids: tuple[int, ...]
    document_id: int


@dataclass
class CapturingLabPersistWriter:
    """Process-level fake of :class:`LabPersistWriter`.

    Captures the validated extraction and returns deterministic ids so
    assertions stay pinned. The fake records a single call (single
    extraction per test); a future multi-call test would extend this
    to a list.
    """

    next_order_id: int = 9001
    next_report_id: int = 9002
    base_result_id: int = 9100

    captured: list[Any] | None = None  # populated lazily; one entry per call

    async def persist(self, *, extraction: Any) -> PersistResult:
        if self.captured is None:
            self.captured = []
        self.captured.append(extraction)
        result_ids = tuple(
            self.base_result_id + i for i, _ in enumerate(extraction.values)
        )
        return PersistResult(
            procedure_order_id=self.next_order_id,
            procedure_report_id=self.next_report_id,
            procedure_result_ids=result_ids,
            document_id=extraction.document_id,
        )


# ---------------------------------------------------------------------------
# Audit-event recorder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """One audit-event record. ``name`` is the closed-set event name
    (e.g. ``"document_ingest"``, ``"lab_persist"``). ``payload`` carries
    the structural metadata for that event (document_id, patient_id,
    counts) — never PHI.
    """

    name: str
    payload: dict[str, Any]


@dataclass
class CapturingAuditRecorder:
    """In-memory audit-event sink the test uses in lieu of the real
    audit logger seam.

    The OpenEMR-side audit (``LabPersistAuditWriter`` writing to the
    ``log`` table) is exercised by PHP unit tests; the Python side has
    no production audit-event surface yet. This recorder mocks what
    that future surface would look like — a method per event name,
    each pushing an :class:`AuditEvent` onto an ordered list. The test
    asserts both presence and ORDER (document_ingest before
    lab_persist).
    """

    events: list[AuditEvent] | None = None

    def _ensure(self) -> list[AuditEvent]:
        if self.events is None:
            self.events = []
        return self.events

    def record_document_ingest(
        self,
        *,
        document_id: int,
        patient_id: int,
        doc_type: str,
        byte_count: int,
    ) -> None:
        self._ensure().append(
            AuditEvent(
                name="document_ingest",
                payload={
                    "document_id": document_id,
                    "patient_id": patient_id,
                    "doc_type": doc_type,
                    "byte_count": byte_count,
                },
            )
        )

    def record_lab_persist(
        self,
        *,
        document_id: int,
        patient_id: int,
        procedure_order_id: int,
        procedure_result_ids: tuple[int, ...],
        extraction_status: str,
    ) -> None:
        self._ensure().append(
            AuditEvent(
                name="lab_persist",
                payload={
                    "document_id": document_id,
                    "patient_id": patient_id,
                    "procedure_order_id": procedure_order_id,
                    "procedure_result_ids": list(procedure_result_ids),
                    "extraction_status": extraction_status,
                },
            )
        )

    @property
    def event_names(self) -> list[str]:
        return [e.name for e in (self.events or [])]
