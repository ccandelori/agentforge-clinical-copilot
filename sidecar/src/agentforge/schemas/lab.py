"""W2 lab-PDF extraction schemas.

The lab worker (Task 11) consumes a scanned lab-PDF via the vision
tool and emits a :class:`LabPdfExtraction` whose every value carries a
:class:`Citation` back to its source bbox. The persistence endpoint
(Task 8) then writes the values into OpenEMR — the exact target table
is Task 8's call (procedure_result vs an intermediate "unapproved
lab" table); whatever the choice, the worker output here is the wire
shape it consumes.

The shape mirrors :class:`agentforge.schemas.intake.IntakeFormExtraction`
where it can: every leaf carries a Citation, structurally optional
fields stay optional, and `unsupported_fields` captures the worker's
"tried but couldn't extract cleanly" surface so the synthesizer can
surface absence to the clinician explicitly. See
W2_ARCHITECTURE.md §2.3 (lab path).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from agentforge.schemas.citation import Citation


class AbnormalFlag(StrEnum):
    """Closed set of abnormal-flag markers that lab reports actually
    print. The two `critical_*` entries cover values flagged for
    immediate clinical attention (e.g. potassium > 6.5, blood glucose
    < 40); the synthesizer escalates differently for those than for a
    plain HIGH/LOW.

    UNKNOWN is the default for fields the lab didn't flag — we do NOT
    invent normality from a missing flag, since some panels print
    abnormal values without explicit flags.
    """

    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"
    UNKNOWN = "unknown"


class LabValue(BaseModel):
    """A single lab result row from a scanned lab PDF.

    `loinc_code` is optional because not every lab report prints LOINC
    codes — small reference labs and ad-hoc panels routinely omit them.
    The agent treats LOINC presence as a confidence boost on the
    test_name match, not as a requirement.

    `unit` and `reference_range` are likewise optional. `collection_date`
    is a date (not datetime) because lab PDFs report by day, not
    timestamp; if precision matters elsewhere it lives in the source
    text the citation points at.

    `abnormal_flag` defaults to UNKNOWN. The persistence endpoint and
    synthesizer do NOT treat UNKNOWN as "normal" — see the AbnormalFlag
    docstring for the rationale.
    """

    test_name: str
    loinc_code: str | None = None
    value: str
    """Stringified value (preserves units-in-line, ranges, "Not Detected", etc.)."""

    unit: str | None = None
    reference_range: str | None = None
    collection_date: date | None = None
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN
    citation: Citation


class LabPdfExtraction(BaseModel):
    """The lab worker's structured output for one scanned lab PDF.

    `document_id` and `patient_id` are required so the persistence
    endpoint (Task 8) can perform the JWT-vs-payload-vs-document
    triple-check before writing. Both come from the OpenEMR session at
    upload time.

    `ordering_provider` and `accession_number` are optional because
    not every lab PDF surfaces them in a form the vision tool can
    extract reliably. `accession_number` in particular is often a
    barcode or low-contrast print run; treating it as best-effort
    avoids inventing identifiers.

    `extraction_confidence` is the worker's overall self-rating across
    the PDF, clamped [0, 1]. `unsupported_fields` is the same
    anti-invention surface as on IntakeFormExtraction — fields the
    worker tried to extract but couldn't map confidently land here as
    string keys for the synthesizer to surface.
    """

    document_id: int
    patient_id: int
    ordering_provider: str | None = None
    accession_number: str | None = None
    values: list[LabValue] = Field(default_factory=list)
    extraction_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Worker's overall self-rating across the PDF [0, 1].",
    )
    unsupported_fields: list[str] = Field(default_factory=list)
