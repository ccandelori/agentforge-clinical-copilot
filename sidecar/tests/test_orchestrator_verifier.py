"""Verifier wiring inside the orchestrator turn loop.

Once the LLM stops emitting tool_use, the orchestrator builds a
CitationIndex from the tool results it collected this turn and runs
StreamingVerifier (with the configured DomainConstraints) over the
final assistant text. Sentences that don't ground are replaced with
the redaction marker before the user sees them. See ARCHITECTURE.md S6.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse, ToolCall
from agentforge.orchestrator import Orchestrator
from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResultMetadata
from agentforge.tools.medications import (
    MedicationItem,
    MedicationsPayload,
    MedicationsResult,
)
from agentforge.tools.problems import ProblemItem, ProblemsPayload, ProblemsResult

REJECTION_MARKER = "[claim withheld — could not be grounded]"


def _ctx(*, patient_id: int = 7, user_id: int = 42) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        patient_id=patient_id,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _meta(name: str) -> ToolResultMetadata:
    return ToolResultMetadata(
        tool_name=name,
        fetched_at=datetime.now(UTC),
        data_freshness_seconds=60,
        source=f"openemr.{name}",
    )


def _demographics(patient_id: int = 7) -> DemographicsResult:
    return DemographicsResult(
        metadata=_meta("get_demographics"),
        payload=DemographicsPayload(
            patient_id=patient_id,
            given_name="Jane",
            family_name="Doe",
            date_of_birth=date(1980, 5, 1),
        ),
    )


def _problems(*ids: int) -> ProblemsResult:
    return ProblemsResult(
        metadata=_meta("get_active_problems"),
        payload=ProblemsPayload(
            problems=tuple(ProblemItem(id=i, title=f"Problem {i}") for i in ids),
        ),
    )


def _medications(*ids: int) -> MedicationsResult:
    return MedicationsResult(
        metadata=_meta("get_active_medications"),
        payload=MedicationsPayload(
            medications=tuple(
                MedicationItem(id=i, name=f"Med {i}") for i in ids
            ),
        ),
    )


def _llm_with_responses(*responses: LLMResponse) -> AsyncMock:
    """Mock LLMClient that yields the given responses in order on `complete`."""
    mock = AsyncMock()
    mock.complete.side_effect = list(responses)
    return mock


def _fetcher_returning(result: Any) -> AsyncMock:
    mock = AsyncMock()
    mock.fetch.return_value = result
    return mock


def _build_orchestrator(
    *,
    llm: AsyncMock,
    demographics: AsyncMock | None = None,
    problems: AsyncMock | None = None,
    medications: AsyncMock | None = None,
    verifier_enabled: bool = True,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=demographics or AsyncMock(),
        medications_fetcher=medications or AsyncMock(),
        problems_fetcher=problems or AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        verifier_enabled=verifier_enabled,
    )


class TestVerifierBypassWhenDisabled:
    async def test_returns_raw_text_when_verifier_disabled(self) -> None:
        # Even uncited text passes through with verifier_enabled=False —
        # this is the legacy MVP behavior we keep until the citation
        # prompt is the agreed contract.
        llm = _llm_with_responses(
            LLMResponse(
                text="Patient is doing well overall.",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=10,
                output_tokens=8,
            )
        )
        orch = _build_orchestrator(llm=llm, verifier_enabled=False)
        reply = await orch.turn(_ctx(), "How is the patient?")
        assert reply == "Patient is doing well overall."


class TestVerifierGroundsResponseAgainstToolResults:
    async def test_grounded_sentence_passes_through(self) -> None:
        # The model called get_active_problems, and its response cites
        # the returned problem record by ID. The verifier finds the
        # citation in the per-turn cache and lets it through.
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="Active diagnosis includes problem one [problem #1]. ",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=12,
            ),
        )
        problems = _fetcher_returning(_problems(1))
        orch = _build_orchestrator(llm=llm, problems=problems)
        reply = await orch.turn(_ctx(), "What problems does the patient have?")
        assert "problem #1" in reply
        assert REJECTION_MARKER not in reply

    async def test_uncited_sentence_is_redacted(self) -> None:
        # Same shape, but the model emits a sentence with no citation.
        # The verifier replaces it with the redaction marker.
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="Patient seems generally healthy. ",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=8,
            ),
        )
        problems = _fetcher_returning(_problems(1))
        orch = _build_orchestrator(llm=llm, problems=problems)
        reply = await orch.turn(_ctx(), "How is the patient?")
        assert REJECTION_MARKER in reply

    async def test_fabricated_citation_id_is_rejected(self) -> None:
        # Model cites problem #999 but the tool returned only problem #1.
        # The verifier rejects on citation_not_in_cache.
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text="Hypertension is documented [problem #999]. ",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=10,
            ),
        )
        problems = _fetcher_returning(_problems(1))
        orch = _build_orchestrator(llm=llm, problems=problems)
        reply = await orch.turn(_ctx(), "any HTN?")
        assert REJECTION_MARKER in reply
        assert "999" not in reply

    async def test_mixes_pass_and_redact_in_same_response(self) -> None:
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=2,
            ),
            LLMResponse(
                text=(
                    "Real claim [problem #5]. "
                    "Made-up claim about something else. "
                ),
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=20,
                output_tokens=15,
            ),
        )
        problems = _fetcher_returning(_problems(5))
        orch = _build_orchestrator(llm=llm, problems=problems)
        reply = await orch.turn(_ctx(), "summarize")
        assert "[problem #5]" in reply
        assert REJECTION_MARKER in reply


class TestMultipleToolsBuildIndex:
    async def test_citations_across_two_tools_resolve(self) -> None:
        # Orchestrator collects results from both tools and the verifier
        # should accept citations to either record class.
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="get_active_problems", input={}),
                    ToolCall(id="t2", name="get_active_medications", input={}),
                ],
                stop_reason="tool_use",
                input_tokens=10,
                output_tokens=4,
            ),
            LLMResponse(
                text=(
                    "Diabetes is on file [problem #1]. "
                    "Patient is on metformin [medication #10]. "
                ),
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=30,
                output_tokens=20,
            ),
        )
        problems = _fetcher_returning(_problems(1))
        medications = _fetcher_returning(_medications(10))
        orch = _build_orchestrator(
            llm=llm, problems=problems, medications=medications
        )
        reply = await orch.turn(_ctx(), "summary please")
        assert REJECTION_MARKER not in reply
        assert "[problem #1]" in reply
        assert "[medication #10]" in reply


class TestEmptyResponseHandling:
    async def test_returns_no_response_marker_when_text_is_empty(self) -> None:
        # Empty assistant text shouldn't crash the verifier — the
        # orchestrator returns the legacy "(no response)" marker without
        # routing through the verifier.
        llm = _llm_with_responses(
            LLMResponse(
                text="",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=10,
                output_tokens=0,
            )
        )
        orch = _build_orchestrator(llm=llm, verifier_enabled=True)
        reply = await orch.turn(_ctx(), "?")
        assert reply == "(no response)"


@pytest.fixture(autouse=True)
def _silence_warnings() -> None:
    # Pydantic v2 emits a DeprecationWarning when frozen models are
    # instantiated via positional args in some test paths; we pass
    # everything by keyword so this fixture is a no-op safety belt.
    return None
