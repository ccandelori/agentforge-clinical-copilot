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

from unittest.mock import AsyncMock

from agentforge.orchestrator import Orchestrator
from agentforge.orchestrator.planner import Planner


def _build_orchestrator(*, planner: Planner | None = None) -> Orchestrator:
    """Construct an Orchestrator with all-mock fetchers.

    The factory mirrors ``test_orchestrator_tracing._build`` but pares
    it back to the surface 4.1 needs to assert on. As later subtasks
    require richer collaborators (real Planner with stub LLM, langfuse
    mock with use_case capture), they extend this factory rather than
    forking it.
    """
    return Orchestrator(
        llm=AsyncMock(),
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
        planner=planner,
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
