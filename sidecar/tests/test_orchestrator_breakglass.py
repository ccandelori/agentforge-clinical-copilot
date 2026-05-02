"""Orchestrator wiring for breakglass audit (Task 34).

When a turn arrives with ``ctx.breakglass_flag = True`` and an audit
tool is configured, the orchestrator fires the audit before invoking
the model. The tool itself dedups per session, so the orchestrator
just calls it on every turn — the tool decides whether to write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from agentforge.breakglass import AuditOutcome, BreakglassAuditTool
from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator


def _ctx(*, breakglass_flag: bool, reason: str | None) -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=7,
        username="dr.smith",
        role="clinician",
        breakglass_flag=breakglass_flag,
        breakglass_reason=reason,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=4,
    )


def _llm_with(*responses: LLMResponse) -> AsyncMock:
    mock = AsyncMock()
    mock.complete.side_effect = list(responses)
    return mock


def _build(
    *,
    llm: AsyncMock,
    breakglass_audit: BreakglassAuditTool | AsyncMock | None,
) -> Orchestrator:
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        breakglass_audit=breakglass_audit,
    )


class TestBreakglassFires:
    async def test_audit_invoked_when_breakglass_flag_set(self) -> None:
        audit = AsyncMock()
        audit.log_breakglass_access = AsyncMock(return_value=AuditOutcome.LOGGED)

        orch = _build(
            llm=_llm_with(_final("ok")),
            breakglass_audit=audit,
        )

        await orch.turn(
            _ctx(breakglass_flag=True, reason="Emergency consult"),
            "What's going on?",
            session_id="sess-1",
        )

        audit.log_breakglass_access.assert_awaited_once()
        call_kwargs = audit.log_breakglass_access.await_args.kwargs
        assert call_kwargs.get("session_id") == "sess-1"


class TestBreakglassNoFlag:
    async def test_audit_still_invoked_so_tool_can_decide_no_op(self) -> None:
        # The orchestrator delegates the no-op decision to the tool —
        # cheaper, less branching, and keeps the contract simple. The
        # tool itself returns NO_BREAKGLASS without any side effects.
        audit = AsyncMock()
        audit.log_breakglass_access = AsyncMock(
            return_value=AuditOutcome.NO_BREAKGLASS
        )

        orch = _build(
            llm=_llm_with(_final("ok")),
            breakglass_audit=audit,
        )

        await orch.turn(
            _ctx(breakglass_flag=False, reason=None), "Anything?",
        )

        audit.log_breakglass_access.assert_awaited_once()


class TestBreakglassAuditOptional:
    async def test_orchestrator_works_without_audit_tool_configured(self) -> None:
        # Existing deployments that haven't wired the audit tool yet
        # should keep working. The orchestrator skips the call when
        # the dependency is None.
        orch = _build(
            llm=_llm_with(_final("ok")),
            breakglass_audit=None,
        )

        reply = await orch.turn(
            _ctx(breakglass_flag=True, reason="anything"),
            "test message",
        )

        assert reply == "ok"


class TestBreakglassFailureDoesNotBreakTurn:
    async def test_audit_failure_returned_as_outcome_does_not_raise(self) -> None:
        # The audit tool's contract is "never raises." Even if the
        # underlying call fails, the orchestrator sees an enum value
        # and continues with the turn.
        audit = AsyncMock()
        audit.log_breakglass_access = AsyncMock(
            return_value=AuditOutcome.AUDIT_FAILED
        )

        orch = _build(
            llm=_llm_with(_final("done")),
            breakglass_audit=audit,
        )

        reply = await orch.turn(
            _ctx(breakglass_flag=True, reason="ED consult"),
            "What problems?",
        )

        assert reply == "done"
        audit.log_breakglass_access.assert_awaited_once()
