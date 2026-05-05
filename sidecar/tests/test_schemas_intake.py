"""Tests for the W2 intake-form extraction schemas.

The intake worker (Task 12) parses a scanned intake form into a
structured :class:`IntakeFormExtraction` whose every leaf claim carries
a :class:`Citation` back to the source PDF. The persistence endpoint
then maps it to a FHIR ``QuestionnaireResponse`` against the seeded
canonical Questionnaire (Task 5).

Test scope mirrors the Task 4 test strategy:

1. Empty-list IntakeFormExtraction is valid.
2. MedicationEntry requires name + citation; dose and frequency may be None.
3. chief_concern without chief_concern_citation is valid (both optional).
4. AllergyEntry severity may be None.
5. All four list types accept multiple entries.

We also exercise the IntakeFormExtraction round-trip and confidence range
to lock the contract before downstream tasks consume it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentforge.schemas.citation import Citation, PageBBox, SourceType
from agentforge.schemas.intake import (
    AllergyEntry,
    Demographic,
    FamilyHistoryEntry,
    IntakeFormExtraction,
    MedicationEntry,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _intake_citation(field: str, value: str) -> Citation:
    """High-confidence intake-form citation used as a test default.

    Real extractions get geometry from the vision tool; the tests just
    need *a* valid Citation so the validator doesn't reject the schema
    for a missing one."""
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="document_id_test",
        page_or_section="page 1",
        field_or_chunk_id=field,
        quote_or_value=value,
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.3, bbox_confidence=0.9
        ),
    )


# ---------------------------------------------------------------------------
# Demographic
# ---------------------------------------------------------------------------

def test_demographic_requires_field_value_and_citation() -> None:
    """Demographic is the simplest leaf — three required fields, no
    optional surface."""
    demo = Demographic(
        field="date_of_birth",
        value="1972-04-12",
        citation=_intake_citation("date_of_birth", "April 12, 1972"),
    )
    assert demo.field == "date_of_birth"
    assert demo.value == "1972-04-12"
    assert demo.citation.source_type == SourceType.INTAKE_FORM


def test_demographic_rejects_missing_citation() -> None:
    """Every leaf needs a Citation. The intake form is the source of
    truth; an unattributed demographic field would silently break the
    overlay round-trip."""
    with pytest.raises(ValidationError):
        Demographic(
            field="date_of_birth",
            value="1972-04-12",
        )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# MedicationEntry — Test Strategy #2
# ---------------------------------------------------------------------------

def test_medication_entry_requires_name_and_citation() -> None:
    """name and citation are required; dose and frequency are optional
    because intake forms are inconsistent — patients write "Metformin"
    without a dose all the time."""
    med = MedicationEntry(
        name="Metformin",
        citation=_intake_citation("medications_0_name", "Metformin"),
    )
    assert med.name == "Metformin"
    assert med.dose is None
    assert med.frequency is None


def test_medication_entry_rejects_missing_name() -> None:
    with pytest.raises(ValidationError):
        MedicationEntry(
            citation=_intake_citation("medications_0_name", "Metformin"),
        )  # type: ignore[call-arg]


def test_medication_entry_rejects_missing_citation() -> None:
    with pytest.raises(ValidationError):
        MedicationEntry(
            name="Metformin",
        )  # type: ignore[call-arg]


def test_medication_entry_accepts_full_fields() -> None:
    med = MedicationEntry(
        name="Metformin",
        dose="500mg",
        frequency="BID",
        citation=_intake_citation("medications_0", "Metformin 500mg BID"),
    )
    assert med.dose == "500mg"
    assert med.frequency == "BID"


# ---------------------------------------------------------------------------
# AllergyEntry — Test Strategy #4
# ---------------------------------------------------------------------------

def test_allergy_entry_severity_can_be_none() -> None:
    """Patients often write "Penicillin" with no severity. Forcing a
    value here would mean inventing data — the worker should pass None
    through verbatim."""
    allergy = AllergyEntry(
        substance="Penicillin",
        citation=_intake_citation("allergies_0_substance", "Penicillin"),
    )
    assert allergy.substance == "Penicillin"
    assert allergy.reaction is None
    assert allergy.severity is None


def test_allergy_entry_accepts_full_fields() -> None:
    allergy = AllergyEntry(
        substance="Penicillin",
        reaction="Hives",
        severity="moderate",
        citation=_intake_citation("allergies_0", "Penicillin → hives, moderate"),
    )
    assert allergy.reaction == "Hives"
    assert allergy.severity == "moderate"


def test_allergy_entry_rejects_missing_substance() -> None:
    with pytest.raises(ValidationError):
        AllergyEntry(
            citation=_intake_citation("allergies_0_substance", "Penicillin"),
        )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# FamilyHistoryEntry
# ---------------------------------------------------------------------------

def test_family_history_entry_requires_relative_condition_and_citation() -> None:
    """Both relative and condition are required. "Mother — diabetes" is
    the contract; "Mother — ?" or "? — diabetes" is not actionable."""
    fh = FamilyHistoryEntry(
        relative="mother",
        condition="type 2 diabetes",
        citation=_intake_citation(
            "family_history_0", "Mother: Type 2 diabetes"
        ),
    )
    assert fh.relative == "mother"
    assert fh.condition == "type 2 diabetes"


def test_family_history_entry_rejects_missing_condition() -> None:
    with pytest.raises(ValidationError):
        FamilyHistoryEntry(
            relative="mother",
            citation=_intake_citation("family_history_0", "Mother: ?"),
        )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# IntakeFormExtraction — Test Strategy #1, #3, #5
# ---------------------------------------------------------------------------

def test_intake_form_extraction_accepts_all_empty_lists() -> None:
    """Test Strategy #1. A blank intake form is a valid extraction —
    "the form was scanned, nothing structured was found" is a real
    outcome the synthesizer needs to handle, not an error."""
    extraction = IntakeFormExtraction(
        document_id=42,
        patient_id=7,
        chief_concern=None,
        chief_concern_citation=None,
        demographics=[],
        medications=[],
        allergies=[],
        family_history=[],
        extraction_confidence=0.85,
        unsupported_fields=[],
    )
    assert extraction.demographics == []
    assert extraction.medications == []
    assert extraction.allergies == []
    assert extraction.family_history == []
    assert extraction.unsupported_fields == []


def test_intake_form_extraction_chief_concern_optional_without_citation() -> None:
    """Test Strategy #3. Both chief_concern and chief_concern_citation
    are optional; if a chief concern is present the citation should
    accompany it, but absence of both is a valid empty state.

    Pairing enforcement (concern present implies citation present) is
    out of scope for this contract — the worker layer enforces it. The
    schema itself just permits the optional pair."""
    extraction = IntakeFormExtraction(
        document_id=1,
        patient_id=1,
        chief_concern="chest pain",  # concern present, citation absent
        chief_concern_citation=None,
        demographics=[],
        medications=[],
        allergies=[],
        family_history=[],
        extraction_confidence=0.9,
        unsupported_fields=[],
    )
    assert extraction.chief_concern == "chest pain"
    assert extraction.chief_concern_citation is None


def test_intake_form_extraction_chief_concern_with_citation() -> None:
    citation = _intake_citation("chief_concern", "Chest pain x 2 days")
    extraction = IntakeFormExtraction(
        document_id=1,
        patient_id=1,
        chief_concern="chest pain x 2 days",
        chief_concern_citation=citation,
        demographics=[],
        medications=[],
        allergies=[],
        family_history=[],
        extraction_confidence=0.9,
        unsupported_fields=[],
    )
    assert extraction.chief_concern_citation is not None
    assert extraction.chief_concern_citation.field_or_chunk_id == "chief_concern"


def test_intake_form_extraction_accepts_multiple_entries_per_list() -> None:
    """Test Strategy #5. The four list fields are repeating sections in
    the FHIR Questionnaire (Task 5). Multiple entries must round-trip."""
    medications = [
        MedicationEntry(
            name="Metformin",
            dose="500mg",
            frequency="BID",
            citation=_intake_citation("medications_0", "Metformin 500mg BID"),
        ),
        MedicationEntry(
            name="Lisinopril",
            dose="10mg",
            frequency="QD",
            citation=_intake_citation("medications_1", "Lisinopril 10mg QD"),
        ),
    ]
    allergies = [
        AllergyEntry(
            substance="Penicillin",
            reaction="rash",
            severity="mild",
            citation=_intake_citation("allergies_0", "Penicillin"),
        ),
        AllergyEntry(
            substance="Sulfa",
            citation=_intake_citation("allergies_1", "Sulfa"),
        ),
    ]
    family_history = [
        FamilyHistoryEntry(
            relative="mother",
            condition="type 2 diabetes",
            citation=_intake_citation("family_history_0", "Mother: T2DM"),
        ),
        FamilyHistoryEntry(
            relative="father",
            condition="myocardial infarction",
            citation=_intake_citation("family_history_1", "Father: MI"),
        ),
    ]
    demographics = [
        Demographic(
            field="date_of_birth",
            value="1972-04-12",
            citation=_intake_citation("date_of_birth", "April 12, 1972"),
        ),
        Demographic(
            field="sex",
            value="female",
            citation=_intake_citation("sex", "F"),
        ),
    ]
    extraction = IntakeFormExtraction(
        document_id=1,
        patient_id=1,
        chief_concern=None,
        chief_concern_citation=None,
        demographics=demographics,
        medications=medications,
        allergies=allergies,
        family_history=family_history,
        extraction_confidence=0.88,
        unsupported_fields=["height_cm", "smoking_status"],
    )
    assert len(extraction.demographics) == 2
    assert len(extraction.medications) == 2
    assert len(extraction.allergies) == 2
    assert len(extraction.family_history) == 2
    assert extraction.unsupported_fields == ["height_cm", "smoking_status"]


def test_intake_form_extraction_requires_document_and_patient_ids() -> None:
    """document_id is the foreign key into the persistence endpoint's
    JWT-validated triple-check (Task 12). patient_id likewise. Both are
    structurally required; absence is a programmer error, not a missing
    extraction."""
    with pytest.raises(ValidationError):
        IntakeFormExtraction(
            patient_id=1,
            chief_concern=None,
            chief_concern_citation=None,
            demographics=[],
            medications=[],
            allergies=[],
            family_history=[],
            extraction_confidence=0.9,
            unsupported_fields=[],
        )  # type: ignore[call-arg]


def test_intake_form_extraction_confidence_within_range() -> None:
    """extraction_confidence is the worker's overall self-rating across
    the form. Out-of-range values are bugs, not edge cases."""
    with pytest.raises(ValidationError):
        IntakeFormExtraction(
            document_id=1,
            patient_id=1,
            chief_concern=None,
            chief_concern_citation=None,
            demographics=[],
            medications=[],
            allergies=[],
            family_history=[],
            extraction_confidence=1.5,
            unsupported_fields=[],
        )


def test_intake_form_extraction_rejects_negative_confidence() -> None:
    with pytest.raises(ValidationError):
        IntakeFormExtraction(
            document_id=1,
            patient_id=1,
            chief_concern=None,
            chief_concern_citation=None,
            demographics=[],
            medications=[],
            allergies=[],
            family_history=[],
            extraction_confidence=-0.1,
            unsupported_fields=[],
        )


def test_intake_form_extraction_round_trip() -> None:
    """The verifier and persistence endpoint deserialize this from JSON;
    structural identity through dump/validate is the contract they rely
    on."""
    citation = _intake_citation("chief_concern", "Chest pain")
    original = IntakeFormExtraction(
        document_id=42,
        patient_id=7,
        chief_concern="chest pain",
        chief_concern_citation=citation,
        demographics=[
            Demographic(
                field="date_of_birth",
                value="1972-04-12",
                citation=_intake_citation("date_of_birth", "April 12, 1972"),
            )
        ],
        medications=[
            MedicationEntry(
                name="Metformin",
                citation=_intake_citation("medications_0", "Metformin"),
            )
        ],
        allergies=[
            AllergyEntry(
                substance="Penicillin",
                citation=_intake_citation("allergies_0", "Penicillin"),
            )
        ],
        family_history=[
            FamilyHistoryEntry(
                relative="mother",
                condition="diabetes",
                citation=_intake_citation("family_history_0", "Mother: DM"),
            )
        ],
        extraction_confidence=0.9,
        unsupported_fields=["height_cm"],
    )
    rebuilt = IntakeFormExtraction.model_validate(original.model_dump())
    assert rebuilt == original


# ---------------------------------------------------------------------------
# Public surface: schemas package re-exports the intake models
# ---------------------------------------------------------------------------

def test_public_surface_re_exports_intake_models() -> None:
    """Downstream code imports from `agentforge.schemas` (package-level)
    rather than `agentforge.schemas.intake` (module-level). Locking the
    re-export here prevents silent breakage when the package layout
    changes."""
    from agentforge import schemas

    assert schemas.Demographic is Demographic
    assert schemas.MedicationEntry is MedicationEntry
    assert schemas.AllergyEntry is AllergyEntry
    assert schemas.FamilyHistoryEntry is FamilyHistoryEntry
    assert schemas.IntakeFormExtraction is IntakeFormExtraction
