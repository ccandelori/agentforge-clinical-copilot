"""Tests for the W2 lab-PDF extraction schemas.

The lab worker (Task 11) parses a scanned lab PDF into a structured
:class:`LabPdfExtraction` whose every value carries a :class:`Citation`
back to the source bbox. The persistence endpoint (Task 8) then writes
the values into OpenEMR's `procedure_*` tables (or, for now, a similar
intermediate table — Task 8 will pin the exact target).

Test scope mirrors the Task 3 test strategy:

1. LabValue with a valid Citation passes.
2. LabPdfExtraction with empty values list is valid (a scanned-but-empty
   lab PDF is a real "the form was processed, nothing structured was
   found" outcome).
3. AbnormalFlag coerces from lowercase strings ("high" → HIGH).
4. collection_date accepts ISO-format strings (worker emits 'YYYY-MM-DD').
5. unsupported_fields accumulates low-confidence field names (the
   anti-invention surface — same role as on IntakeFormExtraction).
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from agentforge.schemas.citation import Citation, PageBBox, SourceType
from agentforge.schemas.lab import AbnormalFlag, LabPdfExtraction, LabValue

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _lab_citation(field: str, value: str) -> Citation:
    """High-confidence lab-PDF citation used as a test default. Real
    extractions get geometry from the vision tool; the tests just need
    *a* valid Citation so the validator doesn't reject the schema for a
    missing one."""
    return Citation(
        source_type=SourceType.LAB_PDF,
        source_id="document_id_test",
        page_or_section="page 1",
        field_or_chunk_id=field,
        quote_or_value=value,
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9
        ),
    )


# ---------------------------------------------------------------------------
# AbnormalFlag — Test Strategy #3
# ---------------------------------------------------------------------------

def test_abnormal_flag_has_six_canonical_values() -> None:
    """The closed set covers what lab reports actually print: normal,
    high/low, the critical-* variants for life-threatening values, and
    UNKNOWN as the default for fields the lab didn't flag."""
    assert {member.value for member in AbnormalFlag} == {
        "normal",
        "high",
        "low",
        "critical_high",
        "critical_low",
        "unknown",
    }


def test_abnormal_flag_coerces_from_lowercase_string() -> None:
    """LLM extraction emits the string form. StrEnum lets Pydantic
    coerce it without a custom validator."""
    flag = AbnormalFlag("high")
    assert flag is AbnormalFlag.HIGH


def test_abnormal_flag_coerces_via_pydantic_validate() -> None:
    """Round-trips through model construction with a string value."""
    value = LabValue(
        test_name="Glucose",
        value="120",
        abnormal_flag="high",  # type: ignore[arg-type]
        citation=_lab_citation("glucose", "120 mg/dL"),
    )
    assert value.abnormal_flag is AbnormalFlag.HIGH


def test_abnormal_flag_rejects_unknown_string() -> None:
    """Strings outside the closed set are extraction errors, not
    free-form data."""
    with pytest.raises(ValidationError):
        LabValue(
            test_name="Glucose",
            value="120",
            abnormal_flag="elevated",  # type: ignore[arg-type]
            citation=_lab_citation("glucose", "120 mg/dL"),
        )


# ---------------------------------------------------------------------------
# LabValue — Test Strategy #1
# ---------------------------------------------------------------------------

def test_lab_value_requires_test_name_value_and_citation() -> None:
    """test_name, value, and citation are required. loinc_code, unit,
    reference_range, and collection_date are optional because lab PDFs
    are inconsistent — small reference labs print ad-hoc results that
    skip half these fields."""
    lv = LabValue(
        test_name="Hemoglobin A1C",
        value="9.5",
        citation=_lab_citation("hba1c", "9.5"),
    )
    assert lv.test_name == "Hemoglobin A1C"
    assert lv.value == "9.5"
    assert lv.loinc_code is None
    assert lv.unit is None
    assert lv.reference_range is None
    assert lv.collection_date is None
    assert lv.abnormal_flag is AbnormalFlag.UNKNOWN


def test_lab_value_rejects_missing_test_name() -> None:
    with pytest.raises(ValidationError):
        LabValue(
            value="9.5",
            citation=_lab_citation("hba1c", "9.5"),
        )  # type: ignore[call-arg]


def test_lab_value_rejects_missing_value() -> None:
    with pytest.raises(ValidationError):
        LabValue(
            test_name="Hemoglobin A1C",
            citation=_lab_citation("hba1c", "9.5"),
        )  # type: ignore[call-arg]


def test_lab_value_rejects_missing_citation() -> None:
    """Every lab value needs a Citation. An unattributed numeric value
    in a chart write-up would be the worst-case fabrication mode for
    the agent — fail at the schema."""
    with pytest.raises(ValidationError):
        LabValue(
            test_name="Hemoglobin A1C",
            value="9.5",
        )  # type: ignore[call-arg]


def test_lab_value_accepts_full_fields() -> None:
    lv = LabValue(
        test_name="Hemoglobin A1C",
        loinc_code="4548-4",
        value="9.5",
        unit="%",
        reference_range="<5.7",
        collection_date=date(2026, 4, 15),
        abnormal_flag=AbnormalFlag.HIGH,
        citation=_lab_citation("hba1c", "9.5%"),
    )
    assert lv.loinc_code == "4548-4"
    assert lv.unit == "%"
    assert lv.reference_range == "<5.7"
    assert lv.collection_date == date(2026, 4, 15)
    assert lv.abnormal_flag is AbnormalFlag.HIGH


# ---------------------------------------------------------------------------
# collection_date — Test Strategy #4
# ---------------------------------------------------------------------------

def test_collection_date_accepts_iso_string() -> None:
    """The worker emits ISO 'YYYY-MM-DD'; Pydantic coerces to
    date automatically. Verifying the round-trip locks the wire shape."""
    lv = LabValue(
        test_name="Glucose",
        value="120",
        collection_date="2026-04-15",  # type: ignore[arg-type]
        citation=_lab_citation("glucose", "120"),
    )
    assert lv.collection_date == date(2026, 4, 15)


def test_collection_date_rejects_invalid_string() -> None:
    with pytest.raises(ValidationError):
        LabValue(
            test_name="Glucose",
            value="120",
            collection_date="not-a-date",  # type: ignore[arg-type]
            citation=_lab_citation("glucose", "120"),
        )


# ---------------------------------------------------------------------------
# LabPdfExtraction — Test Strategy #2, #5
# ---------------------------------------------------------------------------

def test_lab_pdf_extraction_accepts_empty_values_list() -> None:
    """Test Strategy #2. A blank lab PDF is a valid extraction —
    "the worker processed it, nothing structured was found" is a real
    outcome the synthesizer needs to handle, not an error."""
    extraction = LabPdfExtraction(
        document_id=42,
        patient_id=7,
        ordering_provider=None,
        accession_number=None,
        values=[],
        extraction_confidence=0.85,
        unsupported_fields=[],
    )
    assert extraction.values == []
    assert extraction.unsupported_fields == []


def test_lab_pdf_extraction_unsupported_fields_accumulates_names() -> None:
    """Test Strategy #5. The worker drops field names it tried to
    extract but couldn't confidently map (low bbox confidence, illegible
    handwriting, value didn't pass a downstream sanity check) into
    unsupported_fields. The synthesizer surfaces those to the clinician
    so the submission flow stays honest about what's missing — same
    role as IntakeFormExtraction.unsupported_fields."""
    extraction = LabPdfExtraction(
        document_id=1,
        patient_id=1,
        ordering_provider=None,
        accession_number=None,
        values=[],
        extraction_confidence=0.5,
        unsupported_fields=["urinalysis_specific_gravity", "blood_smear_morphology"],
    )
    assert extraction.unsupported_fields == [
        "urinalysis_specific_gravity",
        "blood_smear_morphology",
    ]


def test_lab_pdf_extraction_unsupported_fields_default_factory() -> None:
    """Default-factory keeps the empty-list invariant per instance —
    no shared-mutable-default bug."""
    e1 = LabPdfExtraction(
        document_id=1,
        patient_id=1,
        values=[],
        extraction_confidence=0.9,
    )
    e2 = LabPdfExtraction(
        document_id=2,
        patient_id=2,
        values=[],
        extraction_confidence=0.9,
    )
    e1.unsupported_fields.append("leak_test")
    assert e2.unsupported_fields == []


def test_lab_pdf_extraction_accepts_multiple_values() -> None:
    values = [
        LabValue(
            test_name="Hemoglobin A1C",
            value="9.5",
            unit="%",
            abnormal_flag=AbnormalFlag.HIGH,
            citation=_lab_citation("hba1c", "9.5%"),
        ),
        LabValue(
            test_name="Glucose",
            value="180",
            unit="mg/dL",
            abnormal_flag=AbnormalFlag.HIGH,
            citation=_lab_citation("glucose", "180 mg/dL"),
        ),
        LabValue(
            test_name="Creatinine",
            value="1.0",
            unit="mg/dL",
            abnormal_flag=AbnormalFlag.NORMAL,
            citation=_lab_citation("creatinine", "1.0 mg/dL"),
        ),
    ]
    extraction = LabPdfExtraction(
        document_id=42,
        patient_id=7,
        ordering_provider="Dr. Smith",
        accession_number="ACC-2026-04-15-001",
        values=values,
        extraction_confidence=0.92,
        unsupported_fields=[],
    )
    assert len(extraction.values) == 3
    assert extraction.ordering_provider == "Dr. Smith"


def test_lab_pdf_extraction_requires_document_and_patient_ids() -> None:
    """document_id and patient_id are the FKs the persistence endpoint
    triple-checks against the JWT and the documents table. Both are
    structurally required."""
    with pytest.raises(ValidationError):
        LabPdfExtraction(
            patient_id=1,
            values=[],
            extraction_confidence=0.9,
        )  # type: ignore[call-arg]


def test_lab_pdf_extraction_confidence_within_range() -> None:
    with pytest.raises(ValidationError):
        LabPdfExtraction(
            document_id=1,
            patient_id=1,
            values=[],
            extraction_confidence=1.5,
        )


def test_lab_pdf_extraction_round_trip() -> None:
    """The verifier and persistence endpoint deserialize this from JSON;
    structural identity through dump/validate is the contract they rely
    on."""
    original = LabPdfExtraction(
        document_id=42,
        patient_id=7,
        ordering_provider="Dr. Smith",
        accession_number="ACC-1",
        values=[
            LabValue(
                test_name="Glucose",
                loinc_code="2345-7",
                value="180",
                unit="mg/dL",
                reference_range="70-100",
                collection_date=date(2026, 4, 15),
                abnormal_flag=AbnormalFlag.HIGH,
                citation=_lab_citation("glucose", "180 mg/dL"),
            )
        ],
        extraction_confidence=0.91,
        unsupported_fields=["urinalysis_color"],
    )
    rebuilt = LabPdfExtraction.model_validate(original.model_dump())
    assert rebuilt == original


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def test_public_surface_re_exports_lab_models() -> None:
    """Downstream code imports from `agentforge.schemas` (package-level)
    rather than `agentforge.schemas.lab` (module-level)."""
    from agentforge import schemas

    assert schemas.AbnormalFlag is AbnormalFlag
    assert schemas.LabValue is LabValue
    assert schemas.LabPdfExtraction is LabPdfExtraction
