"""W2 graph cutover wiring on ``Orchestrator.turn`` (Task 1, MR 6).

The orchestrator gains an optional ``agent_graph`` constructor param
and two W2 input kwargs on ``turn`` (``pdf_pages`` / ``evidence_query``).
When the graph is wired AND a W2 input is set, the iterative tool-use
loop is skipped; the graph drives the turn.

These tests pin the routing decision (W1 vs graph), the starter-state
construction, and the final-text extraction. The graph itself is
exercised in ``test_orchestrator_graph.py`` — here we just prove the
orchestrator hooks the graph correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import Orchestrator
from agentforge.tools.attach_and_extract import RenderedPage


def _ctx(patient_id: int = 8) -> RequestContext:
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


def _rendered_page() -> RenderedPage:
    return RenderedPage(
        page_number=1,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=850,
        pixel_height=1100,
    )


class _RecordingGraph:
    """Stub ``CompiledStateGraph`` that records each ``ainvoke`` call.

    Returns a synthesized result with a single assistant message so the
    orchestrator's final-text extraction has something to find.
    """

    def __init__(self, response_text: str = "graph said hi") -> None:
        self._response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(
        self, state: Any, config: Any = None
    ) -> dict[str, Any]:
        del config
        self.calls.append({"state": state})
        # Mirror what the real graph returns: extend the input messages
        # with the synthesized assistant turn so the orchestrator can
        # find it via _last_assistant_text.
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": self._response_text})
        return {**state, "messages": messages}


def _build_orchestrator(*, agent_graph: Any | None = None) -> Orchestrator:
    """Construct an Orchestrator with the minimum dep set + a noop LLM.

    The noop LLM is used by the W1 path; W2-routed turns never invoke
    it. AsyncMocks for every fetcher because the W1 path may still be
    constructed but isn't expected to fire on cutover-route tests.
    """
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text="w1 response (should not appear in cutover tests)",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )
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
        procedures_fetcher=AsyncMock(),
        agent_graph=agent_graph,
    )


class TestRoutingDecision:
    async def test_pdf_pages_routes_to_graph(self) -> None:
        # When pdf_pages is non-empty AND the graph is wired, the W2
        # graph drives the turn. The W1 LLM client must not be called.
        graph = _RecordingGraph(response_text="extracted from PDF")
        orch = _build_orchestrator(agent_graph=graph)

        reply = await orch.turn(
            _ctx(),
            "what's in this intake form?",
            pdf_pages=[_rendered_page()],
            document_id=42,
        )

        assert reply == "extracted from PDF"
        assert len(graph.calls) == 1

    async def test_evidence_query_routes_to_graph(self) -> None:
        # Even without a PDF, an evidence query alone triggers the
        # W2 path: the graph's evidence_retriever_node will run.
        graph = _RecordingGraph(response_text="answered with evidence")
        orch = _build_orchestrator(agent_graph=graph)

        reply = await orch.turn(
            _ctx(),
            "A1C target?",
            evidence_query="A1C target adult diabetes",
        )

        assert reply == "answered with evidence"
        assert len(graph.calls) == 1

    async def test_no_w2_inputs_uses_w1_loop(self) -> None:
        # The default chart-question turn (no W2 inputs) must still
        # take the W1 iterative path — backwards compatibility.
        graph = _RecordingGraph()
        orch = _build_orchestrator(agent_graph=graph)

        # Plain turn() call — no pdf_pages, no evidence_query.
        # We don't assert the W1 reply text (the iterative loop's
        # behavior is exercised exhaustively elsewhere); just that
        # the graph wasn't touched.
        await orch.turn(_ctx(), "give me the chart overview")

        assert graph.calls == []

    async def test_no_graph_wired_uses_w1_loop_even_with_w2_inputs(
        self,
    ) -> None:
        # Defensive: an orchestrator without agent_graph must NOT try
        # to route W2 inputs through a non-existent graph. Falls back
        # to the W1 loop instead. (Caller is responsible for not
        # supplying W2 inputs in this configuration; the orchestrator
        # tolerates them.)
        orch = _build_orchestrator(agent_graph=None)

        # The W1 loop will run with the AsyncMock LLM (returns end_turn
        # immediately so no real loop iterations happen). We just need
        # to confirm it doesn't raise on the unused W2 inputs.
        reply = await orch.turn(
            _ctx(),
            "anything",
            pdf_pages=[_rendered_page()],
        )

        # The mock LLM's text comes back unchanged through the W1 loop.
        assert "w1 response" in reply


class TestStateConstruction:
    async def test_starter_state_carries_w2_inputs_and_ctx(self) -> None:
        # The orchestrator must hand the graph a fully populated
        # AgentState — pdf_pages, document_id, patient_id (from ctx),
        # evidence_query, and the user message as the first message.
        graph = _RecordingGraph()
        orch = _build_orchestrator(agent_graph=graph)
        page = _rendered_page()

        await orch.turn(
            _ctx(patient_id=99),
            "hello",
            pdf_pages=[page],
            document_id=77,
            evidence_query="diabetes guidelines",
        )

        state = graph.calls[0]["state"]
        assert state["pdf_pages"] == [page]
        assert state["document_id"] == 77
        assert state["patient_id"] == 99
        assert state["query"] == "diabetes guidelines"
        assert state["messages"] == [{"role": "user", "content": "hello"}]
        # Per-turn fields start at zero / empty / sentinel.
        assert state["iteration"] == 0
        assert state["extraction_result"] is None
        assert state["evidence_chunks"] == []
        assert state["tool_results"] == {}


class TestFinalTextExtraction:
    async def test_returns_last_assistant_message_content(self) -> None:
        # The graph appends an assistant message to state.messages.
        # The orchestrator's contract is to return that text verbatim.
        graph = _RecordingGraph(response_text="precise answer with citations.")
        orch = _build_orchestrator(agent_graph=graph)

        reply = await orch.turn(
            _ctx(),
            "anything",
            evidence_query="anything",
        )

        assert reply == "precise answer with citations."

    async def test_returns_sentinel_when_graph_yields_no_assistant(
        self,
    ) -> None:
        # Defensive: if the graph terminates without an assistant
        # message (e.g. a future short-circuit path), the orchestrator
        # returns the same "(no response)" sentinel as the W1 path so
        # callers see a stable contract.
        class _SilentGraph:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def ainvoke(
                self, state: Any, config: Any = None
            ) -> dict[str, Any]:
                del config
                self.calls.append({"state": state})
                # Return state unchanged — no assistant message added.
                return dict(state)

        orch = _build_orchestrator(agent_graph=_SilentGraph())

        reply = await orch.turn(
            _ctx(),
            "anything",
            pdf_pages=[_rendered_page()],
        )

        assert reply == "(no response)"
