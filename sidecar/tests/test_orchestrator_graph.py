"""LangGraph supervisor (Task 1).

MR 1 added the StateGraph skeleton + supervisor routing. MR 2 wires
the real VisionExtractor and EvidenceRetriever into the intake and
evidence worker nodes (synthesize + terminal stay as stubs until
MR 3). Tests use stub planner / extractor / retriever objects so no
LLM or retrieval model is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentforge.orchestrator.graph import (
    MAX_ITERATIONS,
    AgentState,
    RouteDecision,
    build_graph,
    evidence_retriever_node,
    intake_extractor_node,
    supervisor_node,
)
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.rag.types import GuidelineChunk, RetrievalResult
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)


class StubPlanner:
    """Test stand-in for ``Planner``.

    Returns a pre-canned ``Plan`` from ``plan()``. The real ``Planner``
    is duck-typed by the supervisor (only ``plan(user_message)`` is
    used), so this stub is sufficient for graph-level tests.
    """

    def __init__(self, plan: Plan) -> None:
        self._plan = plan
        self.calls: list[str] = []

    async def plan(self, user_message: str) -> Plan:
        self.calls.append(user_message)
        return self._plan


def _empty_followup_plan() -> Plan:
    return Plan(
        use_case=UseCase.FOLLOWUP,
        tool_calls=(),
        parallel_batches=(),
    )


def _starter_state(user_message: str = "hello") -> AgentState:
    return AgentState(
        messages=[{"role": "user", "content": user_message}],
        tool_results=[],
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
        document_id=None,
        patient_id=None,
        pdf_pages=[],
        query="",
    )


def _rendered_page(page_number: int = 1) -> RenderedPage:
    return RenderedPage(
        page_number=page_number,
        png_bytes=b"\x89PNG\r\n\x1a\n",  # PNG magic bytes; content irrelevant in tests
        pixel_width=850,
        pixel_height=1100,
    )


def _intake_extraction_result(
    document_id: int = 42, patient_id: int = 7
) -> VisionExtractionResult[IntakeFormExtraction]:
    return VisionExtractionResult(
        extraction=IntakeFormExtraction(
            document_id=document_id,
            patient_id=patient_id,
            extraction_confidence=0.9,
        ),
        model="claude-test",
        input_tokens=100,
        output_tokens=50,
    )


class StubVisionExtractor:
    """Test stand-in for ``VisionExtractor[IntakeFormExtraction]``."""

    def __init__(
        self, result: VisionExtractionResult[IntakeFormExtraction]
    ) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
    ) -> VisionExtractionResult[IntakeFormExtraction]:
        self.calls.append(
            {
                "pages": pages,
                "document_id": document_id,
                "patient_id": patient_id,
            }
        )
        return self._result


def _starter_state_with_pdf(
    *, document_id: int, patient_id: int, user_message: str = "hello"
) -> AgentState:
    state = _starter_state(user_message=user_message)
    state["document_id"] = document_id
    state["patient_id"] = patient_id
    state["pdf_pages"] = [_rendered_page()]
    return state


def _retrieval_result(chunk_id: str = "c1", score: float = 0.9) -> RetrievalResult:
    chunk = GuidelineChunk.from_index_entry(
        doc_id="ada-2024",
        section="9.1",
        version="2024",
        chunk_id=chunk_id,
        text="A1C target for most adults with diabetes is <7%.",
        token_count=12,
        source_path="ada-2024.pdf",
    )
    return RetrievalResult(chunk=chunk, score=score)


class StubEvidenceRetriever:
    """Test stand-in for ``EvidenceRetriever``."""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]:
        self.calls.append({"query": query, "top_k": top_k})
        return list(self._results)


# ---------------------------------------------------------------------------
# Cycle 1 — tracer bullet: graph compiles + invokes
# ---------------------------------------------------------------------------


class TestGraphTracer:
    @pytest.mark.asyncio
    async def test_build_graph_returns_invocable_graph(self) -> None:
        planner = StubPlanner(_empty_followup_plan())

        graph = build_graph(planner)

        # Compiled langgraph exposes ``ainvoke`` for async invocation.
        # We don't assert on the *result* yet — that's later cycles.
        # Tracer just proves the graph compiles and runs to completion
        # without raising.
        result: dict[str, Any] = await graph.ainvoke(_starter_state())
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Cycle 2 — supervisor consults Planner and records the routing decision
# ---------------------------------------------------------------------------


def _admit_synthesis_plan() -> Plan:
    return Plan(
        use_case=UseCase.ADMIT_SYNTHESIS,
        tool_calls=(),
        parallel_batches=(),
    )


class TestSupervisorPlannerWiring:
    @pytest.mark.asyncio
    async def test_supervisor_invokes_planner_with_user_message(self) -> None:
        planner = StubPlanner(_admit_synthesis_plan())

        graph = build_graph(planner)
        await graph.ainvoke(_starter_state(user_message="why was the patient admitted?"))

        # Supervisor must consult the planner with the user's message.
        # The supervisor runs once per iteration (so several times when
        # workers loop back), but every call must use the same user
        # message — supervisor is stateless w.r.t. the message stream.
        assert planner.calls
        assert all(msg == "why was the patient admitted?" for msg in planner.calls)

    @pytest.mark.asyncio
    async def test_supervisor_writes_route_decision_and_reason(self) -> None:
        planner = StubPlanner(_admit_synthesis_plan())

        graph = build_graph(planner)
        result = await graph.ainvoke(_starter_state())

        # ADMIT_SYNTHESIS is not FOLLOWUP and iteration starts at 0,
        # so MR 1's placeholder routing yields INTAKE_EXTRACTOR.
        # On the second supervisor pass (after the worker loop-back
        # bumps iteration to 1, then 2), the supervisor still picks
        # INTAKE_EXTRACTOR until the cap. The final supervisor pass
        # at iteration >= 3 flips to SYNTHESIZE — that's what we
        # actually observe at graph termination.
        assert result["route_decision"] == RouteDecision.SYNTHESIZE
        assert result["route_reason"]  # non-empty reason recorded


# ---------------------------------------------------------------------------
# Cycle 3 — FOLLOWUP plans short-circuit to SYNTHESIZE
# ---------------------------------------------------------------------------


class TestSupervisorFollowupRouting:
    @pytest.mark.asyncio
    async def test_followup_plan_routes_to_synthesize(self) -> None:
        # Direct supervisor_node invocation isolates the routing rule
        # from graph traversal. With a FOLLOWUP plan and iteration 0,
        # the supervisor must short-circuit to SYNTHESIZE — pure
        # follow-ups don't need tool calls.
        planner = StubPlanner(_empty_followup_plan())
        state = _starter_state()

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.SYNTHESIZE
        assert "followup" in update["route_reason"].lower()


# ---------------------------------------------------------------------------
# Cycle 4 — iteration cap forces SYNTHESIZE regardless of plan
# ---------------------------------------------------------------------------


class TestSupervisorIterationCap:
    @pytest.mark.asyncio
    async def test_at_cap_routes_to_synthesize_even_for_admit(self) -> None:
        # ADMIT_SYNTHESIS would normally route to INTAKE_EXTRACTOR
        # under MR 1's placeholder rule. The cap must override.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state()
        state["iteration"] = MAX_ITERATIONS

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.SYNTHESIZE
        assert "cap" in update["route_reason"].lower()

    @pytest.mark.asyncio
    async def test_just_below_cap_still_routes_to_worker(self) -> None:
        # Sanity counterpart — at iteration MAX-1 the cap has not yet
        # tripped, so the placeholder routing rule still applies.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state()
        state["iteration"] = MAX_ITERATIONS - 1

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.INTAKE_EXTRACTOR


# ---------------------------------------------------------------------------
# Cycle 5 — conditional edges dispatch to the node named by route_decision
# ---------------------------------------------------------------------------


class TestConditionalRouting:
    @pytest.mark.asyncio
    async def test_followup_path_skips_intake_extractor(self) -> None:
        # When supervisor routes straight to synthesize, the intake
        # extractor must never run. Verified by counting calls on the
        # injected stub.
        extractor = StubVisionExtractor(_intake_extraction_result())

        planner = StubPlanner(_empty_followup_plan())
        graph = build_graph(planner, vision_extractor=extractor)
        result = await graph.ainvoke(
            _starter_state_with_pdf(document_id=42, patient_id=7)
        )

        assert extractor.calls == []
        assert result["route_decision"] == RouteDecision.SYNTHESIZE

    @pytest.mark.asyncio
    async def test_admit_path_invokes_intake_extractor(self) -> None:
        # ADMIT_SYNTHESIS routes to intake-extractor; the worker
        # actually drives extraction. With idempotency the extractor
        # is called exactly once even though the loop-back may
        # re-enter the node multiple times.
        extractor = StubVisionExtractor(_intake_extraction_result())

        planner = StubPlanner(_admit_synthesis_plan())
        graph = build_graph(planner, vision_extractor=extractor)
        result = await graph.ainvoke(
            _starter_state_with_pdf(document_id=42, patient_id=7)
        )

        assert len(extractor.calls) == 1
        assert result["extraction_result"] is not None


# ---------------------------------------------------------------------------
# Cycle 6 — workers loop back to supervisor; cap engages at MAX_ITERATIONS
# ---------------------------------------------------------------------------


class TestIterationCapEndToEnd:
    @pytest.mark.asyncio
    async def test_admit_loops_until_cap_then_synthesizes(self) -> None:
        # ADMIT_SYNTHESIS keeps routing to INTAKE_EXTRACTOR until the
        # iteration cap trips and forces SYNTHESIZE. The supervisor
        # runs MAX_ITERATIONS + 1 times (iter 0..MAX, the last one
        # tripping the cap), so the final ``iteration`` field reads
        # MAX_ITERATIONS + 1 — proving the loop-back edge fires that
        # many times. The extractor itself runs exactly once thanks to
        # idempotency (subsequent worker re-entries no-op once
        # extraction_result is set).
        extractor = StubVisionExtractor(_intake_extraction_result())

        planner = StubPlanner(_admit_synthesis_plan())
        graph = build_graph(planner, vision_extractor=extractor)
        result = await graph.ainvoke(
            _starter_state_with_pdf(document_id=42, patient_id=7)
        )

        assert len(extractor.calls) == 1
        assert result["iteration"] == MAX_ITERATIONS + 1
        assert result["route_decision"] == RouteDecision.SYNTHESIZE
        assert "cap" in result["route_reason"].lower()


# ---------------------------------------------------------------------------
# MR 2 — intake_extractor_node wraps VisionExtractor (Task 1.3)
# ---------------------------------------------------------------------------


class TestIntakeExtractorNode:
    @pytest.mark.asyncio
    async def test_calls_vision_extractor_with_state_args(self) -> None:
        # Given a state carrying a rendered page + identifiers, the
        # node must hand them to VisionExtractor.extract verbatim and
        # write the validated extraction into state.
        result = _intake_extraction_result(document_id=42, patient_id=7)
        extractor = StubVisionExtractor(result)
        page = _rendered_page()

        state = _starter_state()
        state["document_id"] = 42
        state["patient_id"] = 7
        state["pdf_pages"] = [page]

        update = await intake_extractor_node(state, extractor)

        assert extractor.calls == [
            {"pages": [page], "document_id": 42, "patient_id": 7}
        ]
        assert update["extraction_result"] == result.extraction

    @pytest.mark.asyncio
    async def test_no_pages_skips_extraction(self) -> None:
        # The supervisor's placeholder rule may route to intake even
        # when no PDF is attached (e.g. pure clinical questions).
        # The node must be silent in that case — never call out to
        # the (expensive) Anthropic API for a no-op turn.
        extractor = StubVisionExtractor(_intake_extraction_result())

        state = _starter_state()
        state["document_id"] = 42
        state["patient_id"] = 7
        # pdf_pages stays empty

        update = await intake_extractor_node(state, extractor)

        assert extractor.calls == []
        assert update == {}

    @pytest.mark.asyncio
    async def test_missing_document_id_skips_extraction(self) -> None:
        # extract() requires both document_id and patient_id (the
        # persistence triple-check needs them). Skip rather than
        # invent placeholder values.
        extractor = StubVisionExtractor(_intake_extraction_result())

        state = _starter_state()
        state["pdf_pages"] = [_rendered_page()]
        state["patient_id"] = 7
        # document_id stays None

        update = await intake_extractor_node(state, extractor)

        assert extractor.calls == []
        assert update == {}

    @pytest.mark.asyncio
    async def test_already_extracted_is_idempotent(self) -> None:
        # The supervisor's loop-back means the worker can be re-entered
        # multiple times. Once extraction has happened, the node must
        # short-circuit to avoid duplicate Anthropic calls.
        extractor = StubVisionExtractor(_intake_extraction_result())
        prior_extraction = IntakeFormExtraction(
            document_id=42,
            patient_id=7,
            extraction_confidence=0.85,
        )

        state = _starter_state()
        state["document_id"] = 42
        state["patient_id"] = 7
        state["pdf_pages"] = [_rendered_page()]
        state["extraction_result"] = prior_extraction

        update = await intake_extractor_node(state, extractor)

        assert extractor.calls == []
        assert update == {}


# ---------------------------------------------------------------------------
# MR 2 — evidence_retriever_node wraps EvidenceRetriever (Task 1.4)
# ---------------------------------------------------------------------------


class TestEvidenceRetrieverNode:
    @pytest.mark.asyncio
    async def test_calls_retriever_with_state_query(self) -> None:
        # Given a query in state, the node hands it to retrieve()
        # and writes the results into evidence_chunks.
        results = [_retrieval_result("c1"), _retrieval_result("c2", score=0.7)]
        retriever = StubEvidenceRetriever(results)

        state = _starter_state()
        state["query"] = "A1C target adult diabetes"

        update = await evidence_retriever_node(state, retriever)

        assert retriever.calls == [
            {"query": "A1C target adult diabetes", "top_k": 5}
        ]
        assert update["evidence_chunks"] == results

    @pytest.mark.asyncio
    async def test_empty_query_skips_retrieval(self) -> None:
        # The placeholder routing rule may send a turn through this
        # node even when no evidence query is set. Skip cleanly —
        # don't pay for a retrieval pass on an empty string.
        retriever = StubEvidenceRetriever([_retrieval_result()])

        state = _starter_state()
        # query stays ""

        update = await evidence_retriever_node(state, retriever)

        assert retriever.calls == []
        assert update == {}

    @pytest.mark.asyncio
    async def test_already_retrieved_is_idempotent(self) -> None:
        # Same idempotency story as the intake extractor: once
        # evidence has been pulled this turn, re-entry must no-op.
        prior = [_retrieval_result("c-prior")]
        retriever = StubEvidenceRetriever([_retrieval_result("c-new")])

        state = _starter_state()
        state["query"] = "anything"
        state["evidence_chunks"] = list(prior)

        update = await evidence_retriever_node(state, retriever)

        assert retriever.calls == []
        assert update == {}
