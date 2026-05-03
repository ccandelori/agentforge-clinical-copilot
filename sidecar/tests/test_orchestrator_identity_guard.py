"""IdentityGuard integration into the orchestrator (week1-gaps Task #7).

The guard already exists as a stand-alone unit
(:mod:`agentforge.orchestrator.identity_guard`) and is unit-tested in
``test_identity_guard.py``. What we exercise here is the wiring:

  * ``Orchestrator`` accepts ``identity_guard_enabled``; default is False
    so existing fixtures keep passing.
  * When enabled, ``turn()`` synchronously fetches demographics, builds
    an :class:`IdentityGuard` bound to the chart owner's name + MRN-
    fallback, and checks the user message BEFORE the tool loop.
  * On a cross-patient reference, the turn short-circuits with the
    guard's refusal text. Tool dispatch is skipped entirely.
  * Demographics-fetch failures fail-skip rather than refuse — the guard
    is a usability layer, not a security one (see identity_guard.py
    docstring).
  * The pre-fetched demographics land in the per-turn tool_results so
    the verifier's citation cache sees them, and prime the redis cache
    so a redundant model-issued get_demographics short-circuits to a
    hit.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator
from agentforge.tools.demographics import (
    DemographicsPayload,
    DemographicsResult,
)
from agentforge.tools.dtos import ToolResultMetadata


def _ctx(*, patient_id: int = 8) -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=patient_id,
        username="dr.smith",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


def _llm_returning(text: str) -> AsyncMock:
    mock = AsyncMock()
    mock.complete.return_value = _final(text)
    return mock


def _demographics_result(
    *, given: str, family: str, patient_id: int = 8
) -> DemographicsResult:
    payload = DemographicsPayload(
        patient_id=patient_id,
        given_name=given,
        family_name=family,
        date_of_birth=date(1980, 1, 1),
    )
    metadata = ToolResultMetadata(
        tool_name="get_demographics",
        fetched_at=__import__("datetime").datetime(
            2026, 5, 2, tzinfo=__import__("datetime").UTC
        ),
        data_freshness_seconds=60,
        source="openemr.demographics",
    )
    return DemographicsResult(metadata=metadata, payload=payload)


def _build(
    *,
    llm: AsyncMock | None = None,
    demographics_fetcher: AsyncMock | None = None,
    identity_guard_enabled: bool = True,
    langfuse: MagicMock | None = None,
) -> Orchestrator:
    if demographics_fetcher is None:
        demographics_fetcher = AsyncMock()
        demographics_fetcher.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )
    return Orchestrator(
        llm=llm or _llm_returning("ok"),
        demographics_fetcher=demographics_fetcher,
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        identity_guard_enabled=identity_guard_enabled,
        langfuse=langfuse,
    )


class TestIdentityGuardRefusesCrossPatient:
    async def test_refuses_when_message_names_a_different_patient(self) -> None:
        # Chart is for Susan Underwood; user types "patient John Smith".
        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )

        llm = _llm_returning("MUST NOT REACH MODEL")
        orch = _build(llm=llm, demographics_fetcher=demographics)

        reply = await orch.turn(_ctx(), "Tell me about patient John Smith")

        assert "scoped to Susan Underwood" in reply
        assert "different patient" in reply
        # The model is never called when the guard refuses — that's
        # the whole point of the pre-loop check.
        llm.complete.assert_not_called()

    async def test_refuses_when_message_references_different_mrn(self) -> None:
        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood", patient_id=8
        )

        llm = _llm_returning("MUST NOT REACH MODEL")
        # ctx.patient_id=8 → MRN fallback is "8", so "MRN 99999" mismatches.
        orch = _build(llm=llm, demographics_fetcher=demographics)

        reply = await orch.turn(_ctx(patient_id=8), "Pull MRN 99999 chart")

        assert "scoped to Susan Underwood" in reply
        llm.complete.assert_not_called()


class TestIdentityGuardAllowsValidTurn:
    async def test_passes_through_to_model_when_message_is_clean(self) -> None:
        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )
        llm = _llm_returning("clinical summary text")
        orch = _build(llm=llm, demographics_fetcher=demographics)

        reply = await orch.turn(
            _ctx(), "What are the active medications?"
        )

        assert reply == "clinical summary text"
        llm.complete.assert_awaited_once()


class TestIdentityGuardFailSkipsOnDemographicsError:
    async def test_skips_guard_when_demographics_fetch_raises(self) -> None:
        # Demographics endpoint flakes — we must not refuse the turn,
        # because the real auth boundary is the tool layer.
        demographics = AsyncMock()
        demographics.fetch.side_effect = RuntimeError("boom")

        llm = _llm_returning("answer")
        orch = _build(llm=llm, demographics_fetcher=demographics)

        # Note: we send a message that WOULD be refused if the guard
        # had a name to bind to. Skip behavior means it goes through.
        reply = await orch.turn(_ctx(), "patient Jane Doe rebill")

        assert reply == "answer"
        llm.complete.assert_awaited_once()


class TestIdentityGuardPrefetchPopulatesToolResults:
    async def test_demographics_prefetch_lands_on_redis_cache(self) -> None:
        # When the redis_storage is wired and demographics are pre-fetched,
        # we want the model's redundant get_demographics call to hit the
        # cache instead of refetching. The prefetch should call cache_set.
        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )
        redis_storage = AsyncMock()
        redis_storage.get_cached_tool_result.return_value = None

        orch = Orchestrator(
            llm=_llm_returning("ok"),
            demographics_fetcher=demographics,
            medications_fetcher=AsyncMock(),
            problems_fetcher=AsyncMock(),
            allergies_fetcher=AsyncMock(),
            labs_fetcher=AsyncMock(),
            vitals_fetcher=AsyncMock(),
            notes_fetcher=AsyncMock(),
            search_notes_fetcher=AsyncMock(),
            encounters_fetcher=AsyncMock(),
            immunizations_fetcher=AsyncMock(),
            procedures_fetcher=AsyncMock(),
            redis_storage=redis_storage,
            hmac_key=b"test-key",  # required for cache writes
            identity_guard_enabled=True,
        )

        await orch.turn(_ctx(), "summarize")

        # Cache write happened with tool_name=get_demographics.
        redis_storage.cache_tool_result.assert_awaited_once()
        kwargs = redis_storage.cache_tool_result.await_args.kwargs
        assert kwargs["tool_name"] == "get_demographics"


class TestIdentityGuardDisabledByDefault:
    async def test_when_disabled_demographics_is_not_called_pre_loop(
        self,
    ) -> None:
        # Existing test fixtures don't stub demographics. Default-off
        # protects them from breaking. _dispatch may still call it if
        # the model asks, but turn() doesn't pre-fetch.
        demographics = AsyncMock()
        # Configure to raise so any premature call surfaces loudly.
        demographics.fetch.side_effect = AssertionError(
            "demographics fetched when guard was disabled"
        )

        orch = _build(
            llm=_llm_returning("ok"),
            demographics_fetcher=demographics,
            identity_guard_enabled=False,
        )

        reply = await orch.turn(_ctx(), "summary")

        assert reply == "ok"
        demographics.fetch.assert_not_called()


class TestIdentityGuardTelemetry:
    async def test_records_decision_on_every_guarded_turn(self) -> None:
        # Both valid AND invalid turns should emit the span — dashboards
        # want baseline traffic alongside refusal rate.
        langfuse = MagicMock()
        # Use a sentinel that satisfies the runtime_checkable Protocol.
        trace_handle = MagicMock(trace_id="t-1")
        langfuse.trace_turn.return_value = trace_handle

        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )

        orch = _build(
            llm=_llm_returning("ok"),
            demographics_fetcher=demographics,
            langfuse=langfuse,
        )

        await orch.turn(_ctx(), "summary")

        langfuse.record_identity_guard_decision.assert_called_once()
        kwargs = langfuse.record_identity_guard_decision.call_args.kwargs
        assert kwargs["is_valid"] is True
        assert kwargs["matched_pattern"] is None

    async def test_records_pattern_on_refusal(self) -> None:
        langfuse = MagicMock()
        trace_handle = MagicMock(trace_id="t-1")
        langfuse.trace_turn.return_value = trace_handle

        demographics = AsyncMock()
        demographics.fetch.return_value = _demographics_result(
            given="Susan", family="Underwood"
        )

        orch = _build(
            llm=_llm_returning("ok"),
            demographics_fetcher=demographics,
            langfuse=langfuse,
        )

        await orch.turn(_ctx(), "Tell me about patient John Smith")

        langfuse.record_identity_guard_decision.assert_called_once()
        kwargs = langfuse.record_identity_guard_decision.call_args.kwargs
        assert kwargs["is_valid"] is False
        assert kwargs["matched_pattern"] == "patient_name"
