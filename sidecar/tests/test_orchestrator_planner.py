"""Planner integration into the orchestrator (week1-gaps Task #4).

The Planner exists (see :mod:`agentforge.orchestrator.planner`) but is
not yet wired into ``Orchestrator.turn``. This file builds up the
integration in subtask order:

* 4.1 — orchestrator accepts an optional ``planner`` parameter and
  stashes it. No behavior change yet.
* 4.2 — Langfuse protocol gains ``use_case`` metadata support.
* 4.3 — ``turn()`` calls ``planner.plan()`` before the tool loop.
* 4.4 — ``plan.use_case`` rides on the trace.
* 4.5 — ``create_app`` constructs the Planner and passes it through.

Each subtask lands as its own TDD red→green pair so the integration is
reviewable in slices and the eval suite can be re-run between subtasks
to confirm no regression in the 6-pass baseline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator
from agentforge.orchestrator.planner import (
    Plan,
    PlannedToolCall,
    Planner,
    UseCase,
)


def _ctx() -> RequestContext:
    """Minimal RequestContext for ``turn()`` invocations."""
    return RequestContext(
        user_id=42,
        patient_id=8,
        username="test-user",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _llm_returning(text: str) -> AsyncMock:
    """LLMClient stub whose first ``complete()`` returns ``text``
    with stop_reason='end_turn' so the orchestrator's tool loop
    exits on the first iteration. This isolates planner behavior
    from the rest of the loop.
    """
    mock = AsyncMock()
    mock.complete.return_value = LLMResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )
    return mock


def _stub_plan() -> Plan:
    """A non-empty Plan the planner stub can hand back."""
    return Plan(
        use_case=UseCase.ADMIT_SYNTHESIS,
        tool_calls=(PlannedToolCall(name="get_demographics"),),
        parallel_batches=(("get_demographics",),),
    )


def _stub_planner(plan: Plan | None = None) -> MagicMock:
    """MagicMock-shaped Planner whose ``plan()`` returns ``plan``."""
    mock = MagicMock(spec=Planner)
    mock.plan = AsyncMock(return_value=plan if plan is not None else _stub_plan())
    return mock


def _build_orchestrator(
    *,
    planner: Planner | MagicMock | None = None,
    llm: AsyncMock | None = None,
) -> Orchestrator:
    """Construct an Orchestrator with all-mock fetchers.

    The factory mirrors ``test_orchestrator_tracing._build`` but pares
    it back to the surface 4.1 needs to assert on. As later subtasks
    require richer collaborators (real Planner with stub LLM, langfuse
    mock with use_case capture), they extend this factory rather than
    forking it.
    """
    return Orchestrator(
        llm=llm or AsyncMock(),
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
        procedures_fetcher=AsyncMock(),
        planner=planner,  # type: ignore[arg-type]
    )


class TestPlannerWiring:
    """Subtask 4.1 — constructor accepts a planner and stashes it."""

    def test_orchestrator_accepts_planner_kwarg(self) -> None:
        """The kwarg is optional, defaults to None, and the value
        stashes on ``self._planner`` for the integration code in 4.3
        to read. This is a pure wiring test — no behavior change.
        """
        planner = Planner(llm=AsyncMock())

        orch = _build_orchestrator(planner=planner)

        assert orch._planner is planner

    def test_orchestrator_planner_defaults_to_none(self) -> None:
        """Omitting the kwarg leaves ``_planner`` as None so the legacy
        no-planner path stays the default until 4.5 wires construction.
        """
        orch = _build_orchestrator()

        assert orch._planner is None


class TestPlannerCallSite:
    """Subtask 4.3 — turn() awaits planner.plan() before the tool loop."""

    async def test_turn_invokes_planner_with_user_message(self) -> None:
        """When a planner is wired, the orchestrator must call
        ``planner.plan(user_message)`` exactly once at the top of the
        turn. The plan's contents are not yet consumed (Task #5 wires
        dispatch); this subtask only asserts the call happens so 4.4
        can rely on a Plan being available for trace metadata.
        """
        planner = _stub_planner()
        llm = _llm_returning("ok")
        orch = _build_orchestrator(planner=planner, llm=llm)

        await orch.turn(_ctx(), "Give me a chart overview.")

        planner.plan.assert_awaited_once_with("Give me a chart overview.")

    async def test_turn_does_not_invoke_planner_when_none(self) -> None:
        """The legacy no-planner path stays untouched: when
        ``planner is None`` the model picks tools as it goes via the
        existing tool-use loop. Asserting "no extra collaborator was
        called" guards against accidental regression of that path.
        """
        llm = _llm_returning("ok")
        orch = _build_orchestrator(planner=None, llm=llm)

        # No exception, no AttributeError on a None planner — just runs
        # through the legacy path and returns the LLM's text.
        result = await orch.turn(_ctx(), "Hello.")

        assert result == "ok"

    async def test_turn_calls_planner_before_tool_loop(self) -> None:
        """The planner must run BEFORE the first LLM call so its output
        can seed dispatch in #5. Capturing the call order on a single
        ``MagicMock`` parent ensures the orchestrator doesn't
        accidentally schedule them concurrently or in the wrong
        sequence — both would invalidate the planner-first contract.
        """
        recorder = MagicMock()
        recorder.plan = AsyncMock(return_value=_stub_plan())
        recorder.complete = AsyncMock(
            return_value=LLMResponse(
                text="ok",
                tool_calls=[],
                stop_reason="end_turn",
                input_tokens=10,
                output_tokens=5,
            )
        )

        planner_stub = MagicMock(spec=Planner)
        planner_stub.plan = recorder.plan

        llm_stub = AsyncMock()
        llm_stub.complete = recorder.complete

        orch = _build_orchestrator(planner=planner_stub, llm=llm_stub)

        await orch.turn(_ctx(), "Hello.")

        # Both calls happen on the same recorder; the call list shows
        # ordering. ``mock_calls`` records every method invocation in
        # order, so plan must precede complete.
        call_names = [call[0] for call in recorder.mock_calls]
        # Filter out internal awaitable-return descriptors mock adds.
        ordered = [n for n in call_names if n in ("plan", "complete")]
        assert ordered[0] == "plan", (
            f"planner.plan must be invoked before LLM.complete; "
            f"saw call order: {ordered}"
        )

    async def test_turn_returns_final_text_when_planner_present(
        self,
    ) -> None:
        """Sanity: with a planner wired, the turn still returns the
        synthesizer's final text. The planner output is consumed
        internally; the user-facing contract is unchanged.
        """
        planner = _stub_planner()
        llm = _llm_returning("hello world")
        orch = _build_orchestrator(planner=planner, llm=llm)

        result = await orch.turn(_ctx(), "Hello.")

        assert result == "hello world"


# Marker — keeps the import set quiet against ruff's
# unused-import warning when test classes get reshuffled.
_ = (Any,)
