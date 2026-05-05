"""W2 intake-form extraction schemas.

The intake worker (Task 12) consumes a scanned intake-form PDF via the
vision tool and emits an :class:`IntakeFormExtraction` whose every leaf
claim carries a :class:`Citation` back to its source bbox. The
persistence endpoint then maps the structured extraction to a FHIR
``QuestionnaireResponse`` against the canonical Questionnaire seeded by
Task 5.

The four list models (:class:`Demographic`, :class:`MedicationEntry`,
:class:`AllergyEntry`, :class:`FamilyHistoryEntry`) mirror the four
repeating sections of the FHIR Questionnaire one-to-one. Adding a list
type here is a coordinated change with the migration's item set.

Optional fields exist where intake forms reasonably leave them blank:
medications without a dose, allergies without a severity, demographic
fields the patient skipped. The schema permits the absence; the
unsupported_fields list captures the cases where the *worker* itself
couldn't extract something cleanly (low VLM confidence, handwriting
illegible, etc.) so the synthesizer can prompt the clinician explicitly
rather than silently dropping data.

See W2_ARCHITECTURE.md §2.3 (intake path) and §2.4 (citation contract).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentforge.schemas.citation import Citation


class Demographic(BaseModel):
    """A single demographic field/value pair with its citation.

    Examples: ``("date_of_birth", "1972-04-12")``, ``("sex", "female")``.
    The field key is a free-form string here — normalization to a canonical
    set lives in the persistence layer where it's mapped to FHIR codings.
    """

    field: str
    value: str
    citation: Citation


class MedicationEntry(BaseModel):
    """A medication the patient reports taking.

    ``dose`` and ``frequency`` are optional because intake forms are
    inconsistent — patients write "Metformin" without a dose all the
    time. Forcing values would mean inventing data; missing fields land
    in the persistence layer's "patient confirms / clinician completes"
    loop, not as fabricated literals.
    """

    name: str
    dose: str | None = None
    frequency: str | None = None
    citation: Citation


class AllergyEntry(BaseModel):
    """An allergy the patient reports.

    ``reaction`` and ``severity`` are optional for the same reason
    :class:`MedicationEntry`'s dose is — patients write "Penicillin"
    with nothing else routinely. The structured fields stay None rather
    than carrying placeholder text.
    """

    substance: str
    reaction: str | None = None
    severity: str | None = None
    citation: Citation


class FamilyHistoryEntry(BaseModel):
    """One family-history entry: relative + condition.

    Both fields are required; "Mother: ?" or "?: diabetes" is not an
    actionable family-history entry, so the worker should drop those
    cases into ``unsupported_fields`` instead.
    """

    relative: str
    condition: str
    citation: Citation


class IntakeFormExtraction(BaseModel):
    """The intake worker's structured output for one scanned form.

    ``document_id`` and ``patient_id`` are required so the persistence
    endpoint (Task 12) can perform the JWT-vs-payload-vs-document
    triple-check before writing anything. Both come from the OpenEMR
    session at upload time; neither is reconstructable from the PDF
    alone.

    ``chief_concern`` and ``chief_concern_citation`` are both optional —
    a blank intake form is a valid extraction, and an unattributed chief
    concern is a known shape (e.g. extracted but bbox confidence below
    the floor; the worker keeps the text and reports the field as
    unsupported elsewhere). Pairing enforcement (concern present ⇒
    citation present) is a worker-layer concern, not a schema one.

    ``unsupported_fields`` is the load-bearing companion to the four
    structured lists: anything the worker tried to extract but couldn't
    confidently map (low bbox confidence, illegible handwriting, value
    didn't pass a downstream validator) lands here as a string key. The
    synthesizer surfaces those to the clinician explicitly so the
    submission flow stays honest about what's missing.
    """

    document_id: int
    patient_id: int

    chief_concern: str | None = None
    chief_concern_citation: Citation | None = None

    demographics: list[Demographic] = Field(default_factory=list)
    medications: list[MedicationEntry] = Field(default_factory=list)
    allergies: list[AllergyEntry] = Field(default_factory=list)
    family_history: list[FamilyHistoryEntry] = Field(default_factory=list)

    extraction_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Worker's overall self-rating across the form [0, 1].",
    )
    unsupported_fields: list[str] = Field(default_factory=list)
