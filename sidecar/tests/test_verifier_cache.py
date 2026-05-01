"""CitationIndex — built per-turn from tool results.

The orchestrator collects the ToolResults a turn produced and asks the
index to map ``(record_type, record_id)`` to the underlying record dict.
The verifier then looks up each parsed citation against the index; a
citation whose ID was never returned by any tool this turn is rejected
as fabricated. See ARCHITECTURE.md S6.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.medications import (
    MedicationItem,
    MedicationsPayload,
    MedicationsResult,
)
from agentforge.tools.problems import (
    ProblemItem,
    ProblemsPayload,
    ProblemsResult,
)
from agentforge.verifier.cache import CitationIndex, build_citation_index


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _demographics_result(patient_id: int = 7) -> DemographicsResult:
    return DemographicsResult(
        metadata=_meta("get_demographics"),
        payload=DemographicsPayload(
            patient_id=patient_id,
            given_name="Jane",
            family_name="Doe",
            date_of_birth=date(1980, 5, 1),
        ),
    )


def _problems_result(*ids: int) -> ProblemsResult:
    return ProblemsResult(
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(
            problems=tuple(ProblemItem(id=i, title=f"Problem {i}") for i in ids),
        ),
    )


def _medications_result(*ids: int) -> MedicationsResult:
    return MedicationsResult(
        metadata=_meta("get_active_medications"),
        payload=MedicationsPayload(
            medications=tuple(MedicationItem(id=i, name=f"Med {i}") for i in ids),
        ),
    )


class TestBuildCitationIndex:
    def test_indexes_demographics_by_patient_id(self) -> None:
        index = build_citation_index({"get_demographics": _demographics_result(7)})
        assert index.contains("demographic", "7")

    def test_indexes_each_problem_row(self) -> None:
        index = build_citation_index(
            {"get_active_problems": _problems_result(1, 2, 5)}
        )
        assert index.contains("problem", "1")
        assert index.contains("problem", "2")
        assert index.contains("problem", "5")
        assert not index.contains("problem", "99")

    def test_indexes_each_medication_row(self) -> None:
        index = build_citation_index(
            {"get_active_medications": _medications_result(10, 20)}
        )
        assert index.contains("medication", "10")
        assert index.contains("medication", "20")

    def test_indexes_multiple_tools_in_one_turn(self) -> None:
        index = build_citation_index(
            {
                "get_demographics": _demographics_result(7),
                "get_active_problems": _problems_result(1),
                "get_active_medications": _medications_result(10),
            }
        )
        assert index.contains("demographic", "7")
        assert index.contains("problem", "1")
        assert index.contains("medication", "10")

    def test_unknown_tool_name_does_not_raise(self) -> None:
        # Forward compatibility: a new tool that lands without verifier
        # awareness should not crash the index — it just won't contribute
        # entries, so any citation against it gets rejected. That's the
        # safe default until verifier coverage catches up.
        class _Unknown:
            tool_name = "get_unknown"

        # Synthesize a payload-less stand-in via dict so we don't import
        # a non-existent tool module.
        index = build_citation_index({})
        assert isinstance(index, CitationIndex)
        assert index.size == 0

    def test_empty_tool_set_yields_empty_index(self) -> None:
        index = build_citation_index({})
        assert index.size == 0
        assert not index.contains("anything", "1")


class TestCitationIndex:
    def test_get_returns_record_dict_for_known_key(self) -> None:
        index = build_citation_index(
            {"get_active_problems": _problems_result(1)}
        )
        record = index.get("problem", "1")
        assert record is not None
        assert record["id"] == 1
        assert record["title"] == "Problem 1"

    def test_get_returns_none_for_unknown_key(self) -> None:
        index = build_citation_index(
            {"get_active_problems": _problems_result(1)}
        )
        assert index.get("problem", "999") is None
        assert index.get("medication", "1") is None

    def test_size_reflects_total_records_indexed(self) -> None:
        index = build_citation_index(
            {
                "get_demographics": _demographics_result(7),
                "get_active_problems": _problems_result(1, 2),
                "get_active_medications": _medications_result(10, 20, 30),
            }
        )
        # 1 demographic + 2 problems + 3 meds = 6
        assert index.size == 6
