"""Tests for the deterministic mock tool layer.

Pin two contracts:
  * Default fixture file validates against every tool's typed payload.
  * Each method returns the right typed result and survives an empty-
    fixture path without raising (except demographics, where the
    payload has no clean empty form).
"""

from __future__ import annotations

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.tools.allergies import AllergiesResult
from agentforge.tools.demographics import DemographicsResult
from agentforge.tools.labs import LabsResult
from agentforge.tools.medications import MedicationsResult
from agentforge.tools.notes import NotesResult
from agentforge.tools.problems import ProblemsResult
from agentforge.tools.search_notes import SearchNotesResult
from agentforge.tools.vitals import VitalsResult
from tests.mocks.tools import FixtureMissingError, MockToolLayer


def _ctx(patient_id: int) -> RequestContext:
    return RequestContext(
        user_id=1,
        patient_id=patient_id,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="test-jwt",
    )


# ---------- Default fixtures load and validate ----------


def test_default_fixtures_load_without_validation_errors() -> None:
    # Smoke test: instantiating with no arguments must succeed against
    # the committed JSON fixture file. Any schema drift between the
    # tools and the fixture lands here.
    layer = MockToolLayer()
    assert layer.has_patient(100)
    assert layer.has_patient(200)


# ---------- Complex chronic patient (100) ----------


async def test_demographics_for_complex_patient() -> None:
    layer = MockToolLayer()
    result = await layer.get_demographics(_ctx(100))
    assert isinstance(result, DemographicsResult)
    assert result.payload.given_name == "Susan"
    assert result.payload.family_name == "Underwood"
    assert result.payload.sex == "F"


async def test_problems_for_complex_patient_returns_three_active_dx() -> None:
    layer = MockToolLayer()
    result = await layer.get_active_problems(_ctx(100))
    assert isinstance(result, ProblemsResult)
    titles = [p.title for p in result.payload.problems]
    assert "Type 2 Diabetes Mellitus" in titles
    assert "Essential Hypertension" in titles
    assert "Chronic Kidney Disease, stage 3" in titles


async def test_medications_for_complex_patient_includes_metformin() -> None:
    layer = MockToolLayer()
    result = await layer.get_active_medications(_ctx(100))
    assert isinstance(result, MedicationsResult)
    names = [m.name for m in result.payload.medications]
    assert any("Metformin" in n for n in names)
    assert any("Lisinopril" in n for n in names)


async def test_allergies_for_complex_patient_includes_penicillin() -> None:
    layer = MockToolLayer()
    result = await layer.get_active_allergies(_ctx(100))
    assert isinstance(result, AllergiesResult)
    assert any(a.name == "Penicillin" for a in result.payload.allergies)


async def test_labs_for_complex_patient_include_a1c_creatinine() -> None:
    layer = MockToolLayer()
    result = await layer.get_recent_labs(_ctx(100))
    assert isinstance(result, LabsResult)
    test_names = [lab.test_name for lab in result.payload.labs]
    assert "Hemoglobin A1c" in test_names
    assert "Creatinine" in test_names


async def test_vitals_for_complex_patient_include_bp_reading() -> None:
    layer = MockToolLayer()
    result = await layer.get_vitals_trend(_ctx(100))
    assert isinstance(result, VitalsResult)
    assert len(result.payload.vitals) == 1
    assert result.payload.vitals[0].systolic == 142


async def test_notes_for_complex_patient_returns_progress_note() -> None:
    layer = MockToolLayer()
    result = await layer.get_recent_notes(_ctx(100))
    assert isinstance(result, NotesResult)
    assert len(result.payload.notes) == 1
    assert result.payload.notes[0].note_type == "progress"


async def test_search_notes_finds_diabetes_match() -> None:
    layer = MockToolLayer()
    result = await layer.search_notes(_ctx(100), query="diabetes")
    assert isinstance(result, SearchNotesResult)
    assert len(result.payload.results) == 1
    assert result.payload.results[0].snippet is not None


async def test_search_notes_finds_renal_match() -> None:
    # The renal hit drives UC-2 (NSAID contraindication question).
    layer = MockToolLayer()
    result = await layer.search_notes(_ctx(100), query="renal")
    assert any("contraindicated" in (h.snippet or "").lower()
               for h in result.payload.results)


async def test_search_notes_with_unknown_query_returns_empty() -> None:
    layer = MockToolLayer()
    result = await layer.search_notes(_ctx(100), query="unicorn")
    assert result.payload.results == ()


# ---------- Sparse patient (200) — empty cases ----------


async def test_sparse_patient_has_demographics() -> None:
    layer = MockToolLayer()
    result = await layer.get_demographics(_ctx(200))
    assert result.payload.given_name == "Alex"


async def test_sparse_patient_problems_is_empty() -> None:
    layer = MockToolLayer()
    result = await layer.get_active_problems(_ctx(200))
    assert result.payload.problems == ()


async def test_sparse_patient_medications_is_empty() -> None:
    layer = MockToolLayer()
    result = await layer.get_active_medications(_ctx(200))
    assert result.payload.medications == ()


async def test_sparse_patient_search_notes_is_empty() -> None:
    layer = MockToolLayer()
    result = await layer.search_notes(_ctx(200), query="anything")
    assert result.payload.results == ()


# ---------- Missing-patient fail-fast ----------


async def test_unknown_patient_raises_fixture_missing() -> None:
    layer = MockToolLayer()
    with pytest.raises(FixtureMissingError):
        await layer.get_demographics(_ctx(9999))


# ---------- Inline fixtures override ----------


async def test_layer_accepts_inline_fixtures_dict() -> None:
    # Tests can override the on-disk fixtures wholesale by passing a
    # dict — useful for adversarial cases that need bespoke data.
    custom = {
        500: {
            "demographics": {
                "patient_id": 500,
                "given_name": "Test",
                "family_name": "Patient",
                "date_of_birth": "1980-01-01",
                "sex": "M",
                "preferred_language": None,
            },
            "problems": {"problems": []},
        }
    }
    layer = MockToolLayer(fixtures=custom)
    result = await layer.get_demographics(_ctx(500))
    assert result.payload.given_name == "Test"
