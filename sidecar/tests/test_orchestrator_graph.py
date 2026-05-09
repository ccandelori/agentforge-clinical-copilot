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

from agentforge.llm.types import LLMResponse, Message, ToolSpec
from agentforge.orchestrator.graph import (
    HANDOFF_START_NODE,
    MAX_ITERATIONS,
    SYNTHESIS_SYSTEM_PROMPT,
    AgentState,
    RouteDecision,
    build_graph,
    build_w2_citation_index,
    evidence_retriever_node,
    intake_extractor_node,
    supervisor_node,
    synthesize_node,
    terminal_node,
)
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.prompts import load_prompt
from agentforge.rag.types import GuidelineChunk, RetrievalResult
from agentforge.schemas.citation import (
    Citation as W2Citation,
)
from agentforge.schemas.citation import (
    PageBBox,
    SourceType,
)
from agentforge.schemas.intake import (
    AllergyEntry,
    Demographic,
    IntakeFormExtraction,
    MedicationEntry,
)
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
        tool_results={},
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
        document_id=None,
        patient_id=None,
        pdf_pages=[],
        query="",
        langfuse_trace=None,
        last_node=HANDOFF_START_NODE,
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
        cost_usd=None,
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
        trace: Any = None,
    ) -> VisionExtractionResult[IntakeFormExtraction]:
        self.calls.append(
            {
                "pages": pages,
                "document_id": document_id,
                "patient_id": patient_id,
                "trace": trace,
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


def _intake_citation(field_id: str, page: int = 1) -> W2Citation:
    """Build a valid INTAKE_FORM citation with a high-confidence bbox."""
    return W2Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="42",
        page_or_section=f"page {page}",
        field_or_chunk_id=field_id,
        quote_or_value="value",
        page_bbox=PageBBox(
            page=page,
            x0=0.1,
            y0=0.1,
            x1=0.5,
            y1=0.2,
            bbox_confidence=0.9,
        ),
    )


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
    """Test stand-in for ``EvidenceRetriever``.

    Implements both the legacy ``retrieve()`` and the stats-augmented
    ``retrieve_with_stats()`` introduced in Task 15.5. The stats path
    is what the node calls in production; ``retrieve()`` stays here
    for any callers / tests that pre-date the change.
    """

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]:
        self.calls.append({"query": query, "top_k": top_k})
        return list(self._results)

    async def retrieve_with_stats(
        self, query: str, *, top_k: int = 5
    ) -> Any:
        from agentforge.rag.evidence_retriever import RetrievalStats

        self.calls.append({"query": query, "top_k": top_k})
        return RetrievalStats(
            results=list(self._results),
            bm25_count=len(self._results),
            dense_count=len(self._results),
            post_rerank_count=len(self._results),
        )


class StubSynthesisLLM:
    """Test stand-in for the synthesizer's LLM dependency.

    Records every ``complete()`` call so tests can assert the
    synthesizer assembled the expected system prompt + message list.
    """

    def __init__(self, response_text: str = "synthesized answer.") -> None:
        self._response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(
            text=self._response_text,
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=50,
        )


# ---------------------------------------------------------------------------
# MR 5 — SYNTHESIS_SYSTEM_PROMPT is loaded from prompts/<active>/graph_synthesizer.md
# ---------------------------------------------------------------------------


class TestSynthesisSystemPromptIsVersioned:
    def test_module_constant_loads_from_prompt_library(self) -> None:
        # The graph's synthesizer prompt must be sourced from
        # prompts/<active>/graph_synthesizer.md so future edits land as
        # reviewable text diffs in the prompt library — not silent
        # changes inside a Python module. The module constant should
        # round-trip exactly through ``load_prompt``.
        assert load_prompt("graph_synthesizer") == SYNTHESIS_SYSTEM_PROMPT

    def test_loaded_prompt_carries_expected_grounding_rules(self) -> None:
        # Sanity check on the file content — guards against the prompt
        # being accidentally truncated to a stub or replaced with the
        # W1 ``synthesizer`` body. Both anchor sentences come from the
        # MR 3 inline body that was externalized in MR 5; if either
        # disappears, the synthesizer's grounding contract has shifted
        # and the change deserves a deliberate prompt-version bump.
        assert "clinical co-pilot" in SYNTHESIS_SYSTEM_PROMPT
        assert "do not invent values" in SYNTHESIS_SYSTEM_PROMPT


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
    async def test_just_below_cap_with_pdf_routes_to_intake_extractor(self) -> None:
        # Sanity counterpart — at iteration MAX-1 the cap has not yet
        # tripped, so a state carrying a pending intake PDF gets routed
        # to the worker that needs to run.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state_with_pdf(document_id=42, patient_id=7)
        state["iteration"] = MAX_ITERATIONS - 1

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.INTAKE_EXTRACTOR

    @pytest.mark.asyncio
    async def test_admit_with_no_w2_inputs_routes_to_synthesize(self) -> None:
        # Real routing (MR 6): when neither pdf_pages nor query is set,
        # there's nothing for the workers to do. Even an ADMIT plan
        # should fall through to SYNTHESIZE — the W1 chart-question
        # surface that the iterative loop handled here is delegated to
        # the W1 path until a chart-question worker lands in MR 7.
        planner = StubPlanner(_admit_synthesis_plan())
        state = _starter_state()  # no W2 inputs

        update = await supervisor_node(state, planner)

        assert update["route_decision"] == RouteDecision.SYNTHESIZE
        assert "complete" in update["route_reason"].lower()


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
    async def test_admit_with_pdf_runs_intake_then_synthesizes(self) -> None:
        # Real MR 6 routing: with a pdf_pages input pending, the
        # supervisor routes to INTAKE_EXTRACTOR; on loop-back the
        # extraction is now populated, so routing falls through to
        # SYNTHESIZE without waiting for the iteration cap to fire.
        # The extractor runs exactly once.
        extractor = StubVisionExtractor(_intake_extraction_result())

        planner = StubPlanner(_admit_synthesis_plan())
        graph = build_graph(planner, vision_extractor=extractor)
        result = await graph.ainvoke(
            _starter_state_with_pdf(document_id=42, patient_id=7)
        )

        assert len(extractor.calls) == 1
        assert result["extraction_result"] is not None
        assert result["route_decision"] == RouteDecision.SYNTHESIZE
        assert "complete" in result["route_reason"].lower()


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

        # ``trace=None`` because the starter state carries no
        # langfuse_trace; the node still hands it through so the
        # extractor's telemetry path is reachable when wired.
        assert extractor.calls == [
            {
                "pages": [page],
                "document_id": 42,
                "patient_id": 7,
                "trace": None,
            }
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
        assert update == {"last_node": RouteDecision.INTAKE_EXTRACTOR.value}

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
        assert update == {"last_node": RouteDecision.INTAKE_EXTRACTOR.value}

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
        assert update == {"last_node": RouteDecision.INTAKE_EXTRACTOR.value}


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
        assert update == {"last_node": RouteDecision.EVIDENCE_RETRIEVER.value}

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
        assert update == {"last_node": RouteDecision.EVIDENCE_RETRIEVER.value}


# ---------------------------------------------------------------------------
# MR 3 — synthesize_node calls LLM and appends an assistant message (Task 1.5)
# ---------------------------------------------------------------------------


class TestSynthesizeNode:
    @pytest.mark.asyncio
    async def test_calls_llm_and_appends_assistant_message(self) -> None:
        # Given some user messages in state and no prior assistant
        # turn, synthesize_node must call the LLM exactly once and
        # append the response text as an assistant message.
        llm = StubSynthesisLLM(response_text="The patient's A1C is 7.2%.")

        state = _starter_state(user_message="summarize the chart")

        update = await synthesize_node(state, llm)

        assert len(llm.calls) == 1
        assert update["messages"][-1] == {
            "role": "assistant",
            "content": "The patient's A1C is 7.2%.",
        }
        # Original user message is preserved.
        assert update["messages"][0] == {
            "role": "user",
            "content": "summarize the chart",
        }

    @pytest.mark.asyncio
    async def test_extraction_result_is_surfaced_to_llm(self) -> None:
        # When the intake extractor has produced data this turn, the
        # synthesizer must include it in the LLM prompt so the answer
        # can ground in the extracted values. Verified by inspecting
        # the message list the LLM sees.
        llm = StubSynthesisLLM()
        extraction = IntakeFormExtraction(
            document_id=42,
            patient_id=7,
            extraction_confidence=0.95,
            chief_concern="recurring headaches for 3 weeks",
        )

        state = _starter_state(user_message="what's the chief concern?")
        state["extraction_result"] = extraction

        await synthesize_node(state, llm)

        assert len(llm.calls) == 1
        # The extracted chief concern must surface somewhere in the
        # message stream the LLM sees — either as a context message
        # or appended to the system prompt. We're agnostic about the
        # mechanism, just the observable property.
        all_text = llm.calls[0]["system"] + "".join(
            m.content for m in llm.calls[0]["messages"]
        )
        assert "recurring headaches for 3 weeks" in all_text

    @pytest.mark.asyncio
    async def test_evidence_chunks_surfaced_with_citation_tags(self) -> None:
        # Retrieved guideline chunks must reach the LLM with a stable
        # citation marker so the model can refer back to them in its
        # answer. We don't pin the exact tag format — just the
        # observable property: the chunk's text is present, AND the
        # chunk's doc_id + chunk_id appear nearby so the LLM has a
        # handle to cite.
        llm = StubSynthesisLLM()
        result = _retrieval_result(chunk_id="ada-9-1-stmt-2")

        state = _starter_state(user_message="A1C target?")
        state["evidence_chunks"] = [result]

        await synthesize_node(state, llm)

        all_text = llm.calls[0]["system"] + "".join(
            m.content for m in llm.calls[0]["messages"]
        )
        assert result.chunk.text in all_text
        assert result.chunk.doc_id in all_text
        assert result.chunk.chunk_id in all_text

    @pytest.mark.asyncio
    async def test_already_synthesized_is_idempotent(self) -> None:
        # Synthesize→terminal→END means re-entry shouldn't happen
        # under the current routing, but defending against it cheaply
        # keeps the same idempotency contract every other worker has.
        # If the last message is already an assistant turn, skip.
        llm = StubSynthesisLLM()

        state = _starter_state(user_message="hi")
        state["messages"].append({"role": "assistant", "content": "prior answer"})

        update = await synthesize_node(state, llm)

        assert llm.calls == []
        assert update == {"last_node": RouteDecision.SYNTHESIZE.value}


# ---------------------------------------------------------------------------
# MR 3 — synthesize integration through build_graph
# ---------------------------------------------------------------------------


class TestSynthesizeIntegration:
    @pytest.mark.asyncio
    async def test_followup_turn_produces_synthesized_assistant_message(self) -> None:
        # The shortest path through the graph: FOLLOWUP routes
        # straight to synthesize. With synthesis_llm injected, the
        # final state must carry the assistant answer.
        llm = StubSynthesisLLM(response_text="hi back.")

        planner = StubPlanner(_empty_followup_plan())
        graph = build_graph(planner, synthesis_llm=llm)
        result = await graph.ainvoke(_starter_state(user_message="hi"))

        assert len(llm.calls) == 1
        assert result["messages"][-1] == {
            "role": "assistant",
            "content": "hi back.",
        }


# ---------------------------------------------------------------------------
# MR 5 — SynthesisInputTruncator + DataQualityChecker wired into synthesize_node
# ---------------------------------------------------------------------------


class _RecordingTruncator:
    """Test stub: records each ``truncate`` call without changing input.

    Sufficient to verify the wiring threads ``state['tool_results']`` and
    ``max_synthesis_tokens`` through to the truncator without depending
    on the real :class:`SynthesisInputTruncator`'s tiktoken machinery.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def truncate(
        self,
        results: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        self.calls.append({"results": results, "max_tokens": max_tokens})
        return results


def _stale_lab_tool_results() -> dict[str, Any]:
    """W1-shaped tool_results carrying one HbA1c lab from 2026-03-15.

    Built locally rather than imported from
    ``test_orchestrator_data_quality`` so this file stays standalone.
    """
    from datetime import UTC, date, datetime

    from agentforge.tools.dtos import ToolResultMetadata
    from agentforge.tools.labs import LabResultItem, LabsPayload, LabsResult

    lab = LabResultItem(
        id=1,
        order_id=1,
        report_id=1,
        test_name="HbA1c",
        value="7.5",
        units="%",
        date=date(2026, 3, 15),  # ~48 days before 2026-05-02
    )
    return {
        "get_recent_labs": LabsResult(
            metadata=ToolResultMetadata(
                tool_name="get_recent_labs",
                fetched_at=datetime(2026, 5, 2, tzinfo=UTC),
                data_freshness_seconds=60,
                source="openemr.get_recent_labs",
            ),
            payload=LabsPayload(labs=(lab,)),
        )
    }


def _frozen_data_quality_checker() -> Any:
    """DataQualityChecker frozen to 2026-05-02 with the default 30-day cap.

    Returns Any to avoid importing ``DataQualityChecker`` at module top —
    keeps the test file's import block focused on graph-level surfaces.
    """
    from datetime import UTC, datetime

    from agentforge.verifier.data_quality import DataQualityChecker

    return DataQualityChecker(
        now=lambda: datetime(2026, 5, 2, tzinfo=UTC),
        stale_lab_threshold_days=30,
    )


class TestSynthesisTruncatorWiring:
    @pytest.mark.asyncio
    async def test_truncator_called_with_tool_results_and_cap(self) -> None:
        # When the synthesis node has both a wired truncator AND a
        # non-empty tool_results dict, it must hand them off to the
        # truncator with the configured cap. This wiring is the seam
        # the MR 6 cutover bridge will rely on once W1 callers populate
        # tool_results in graph state.
        llm = StubSynthesisLLM()
        truncator = _RecordingTruncator()
        tool_results = _stale_lab_tool_results()

        state = _starter_state()
        state["tool_results"] = tool_results

        await synthesize_node(
            state, llm, truncator=truncator, max_synthesis_tokens=4_096
        )

        assert len(truncator.calls) == 1
        assert truncator.calls[0]["results"] is tool_results
        assert truncator.calls[0]["max_tokens"] == 4_096

    @pytest.mark.asyncio
    async def test_no_call_when_tool_results_empty(self) -> None:
        # Pure W2 turns carry no tool_results today. With nothing to
        # truncate, the wiring must skip the call entirely so we don't
        # pay tiktoken's encoding cost on a guaranteed no-op.
        llm = StubSynthesisLLM()
        truncator = _RecordingTruncator()

        state = _starter_state()  # tool_results == {}

        await synthesize_node(state, llm, truncator=truncator)

        assert truncator.calls == []

    @pytest.mark.asyncio
    async def test_no_call_when_truncator_not_wired(self) -> None:
        # Backwards-compat sanity: tests / callers that don't pass a
        # truncator must keep working unchanged. Same shape as the MR 4
        # graph behavior.
        llm = StubSynthesisLLM()

        state = _starter_state()
        state["tool_results"] = _stale_lab_tool_results()

        update = await synthesize_node(state, llm)

        assert "messages" in update  # synthesis still ran


class TestSynthesisDataQualityWiring:
    @pytest.mark.asyncio
    async def test_stale_lab_warning_prepended_as_system_reminder(self) -> None:
        # When the data-quality checker fires a stale-lab warning, the
        # synthesizer's system prompt must carry it inside a
        # ``<system_reminder>`` block above the base prompt — that's
        # what gives the model a chance to surface it inline. The base
        # prompt content must stay unmodified so the grounding contract
        # doesn't change just because a flag fired.
        llm = StubSynthesisLLM()
        checker = _frozen_data_quality_checker()

        state = _starter_state()
        state["tool_results"] = _stale_lab_tool_results()

        await synthesize_node(state, llm, data_quality_checker=checker)

        assert len(llm.calls) == 1
        system = llm.calls[0]["system"]
        assert "<system_reminder>" in system
        assert "</system_reminder>" in system
        assert "2026-03-15" in system  # the lab's date appears in the warning
        # Base prompt body must still be present, AFTER the reminder.
        reminder_end = system.index("</system_reminder>")
        assert "clinical co-pilot" in system[reminder_end:]

    @pytest.mark.asyncio
    async def test_no_warnings_no_reminder_block(self) -> None:
        # When DQ runs but no warnings fire, the system prompt must
        # round-trip exactly to the base SYNTHESIS_SYSTEM_PROMPT — no
        # empty reminder block, no leading whitespace, no surprise.
        llm = StubSynthesisLLM()
        checker = _frozen_data_quality_checker()

        state = _starter_state()
        # tool_results stays empty so neither check fires.

        await synthesize_node(state, llm, data_quality_checker=checker)

        assert llm.calls[0]["system"] == SYNTHESIS_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_dq_metrics_recorded_on_langfuse_when_wired(self) -> None:
        # When both the checker and a langfuse client are wired, the
        # synthesizer must call record_data_quality_metrics with the
        # correct counts — even when zero warnings fire (so dashboards
        # see "DQ ran, found nothing" not "DQ never ran").
        from unittest.mock import MagicMock

        llm = StubSynthesisLLM()
        checker = _frozen_data_quality_checker()
        langfuse = MagicMock()
        trace = MagicMock(trace_id="t-1")

        state = _starter_state()
        state["tool_results"] = _stale_lab_tool_results()
        state["langfuse_trace"] = trace

        await synthesize_node(
            state, llm, data_quality_checker=checker, langfuse=langfuse
        )

        langfuse.record_data_quality_metrics.assert_called_once()
        kwargs = langfuse.record_data_quality_metrics.call_args.kwargs
        assert kwargs["stale_labs_count"] == 1
        assert kwargs["conflict_count"] == 0


class TestSupervisorHandoffSpans:
    @pytest.mark.asyncio
    async def test_supervisor_records_handoff_span_on_each_decision(self) -> None:
        # The supervisor must emit one record_handoff_span call per
        # routing decision, capturing the from/to node pair, the
        # decision string, the reason text, and the post-bump iteration
        # counter. This is what makes the trace dashboard's per-handoff
        # view possible.
        from unittest.mock import MagicMock

        langfuse = MagicMock()
        trace = MagicMock(trace_id="t-1")
        planner = StubPlanner(_empty_followup_plan())

        state = _starter_state()
        state["langfuse_trace"] = trace

        await supervisor_node(state, planner, langfuse=langfuse)

        langfuse.record_handoff_span.assert_called_once()
        kwargs = langfuse.record_handoff_span.call_args.kwargs
        assert kwargs["from_node"] == HANDOFF_START_NODE
        assert kwargs["to_node"] == RouteDecision.SYNTHESIZE.value
        assert kwargs["route_decision"] == RouteDecision.SYNTHESIZE.value
        assert "followup" in kwargs["route_reason"].lower()
        assert kwargs["iteration"] == 1  # post-bump

    @pytest.mark.asyncio
    async def test_supervisor_skips_record_when_trace_missing(self) -> None:
        # Symmetric guard: with langfuse wired but state.langfuse_trace
        # still None (Null path or pre-trace turn), no spans should fire.
        # Otherwise the Null implementation gets called with a fake trace
        # and the trace_id stays None — confusing the dashboard.
        from unittest.mock import MagicMock

        langfuse = MagicMock()
        planner = StubPlanner(_empty_followup_plan())

        state = _starter_state()  # langfuse_trace == None

        await supervisor_node(state, planner, langfuse=langfuse)

        langfuse.record_handoff_span.assert_not_called()

    @pytest.mark.asyncio
    async def test_handoff_span_carries_worker_last_node(self) -> None:
        # On the second supervisor pass after a worker loop-back, the
        # handoff span's from_node must reflect the worker that just
        # ran — not the constant HANDOFF_START_NODE marker. This is how
        # the trace shows "intake-extractor → synthesize" instead of
        # "start → synthesize" on the loop-back.
        from unittest.mock import MagicMock

        langfuse = MagicMock()
        trace = MagicMock(trace_id="t-1")
        planner = StubPlanner(_empty_followup_plan())

        state = _starter_state()
        state["langfuse_trace"] = trace
        state["last_node"] = RouteDecision.INTAKE_EXTRACTOR.value
        state["iteration"] = 1  # already past the first supervisor pass

        await supervisor_node(state, planner, langfuse=langfuse)

        kwargs = langfuse.record_handoff_span.call_args.kwargs
        assert kwargs["from_node"] == RouteDecision.INTAKE_EXTRACTOR.value


# ---------------------------------------------------------------------------
# MR 4 — W2 citation index builder (Task 1.6 prep)
# ---------------------------------------------------------------------------


class TestBuildW2CitationIndex:
    def test_empty_state_yields_empty_index(self) -> None:
        # No evidence and no extraction → no entries. Index is still
        # a real CitationIndex instance, not None.
        state = _starter_state()

        index = build_w2_citation_index(state)

        assert index.size == 0

    def test_evidence_chunks_register_under_guideline_key(self) -> None:
        # Each retrieved chunk's W2 citation must show up under
        # ("guideline", chunk_id) — that's the same shape the W1
        # parser produces from a synthesizer-emitted "[guideline #c1]"
        # tag, so the verifier's contains() check resolves cleanly.
        result_a = _retrieval_result(chunk_id="ada-9-1-stmt-2")
        result_b = _retrieval_result(chunk_id="kdigo-3-2-rec")

        state = _starter_state()
        state["evidence_chunks"] = [result_a, result_b]

        index = build_w2_citation_index(state)

        assert index.size == 2
        assert index.contains("guideline", "ada-9-1-stmt-2")
        assert index.contains("guideline", "kdigo-3-2-rec")

    def test_extraction_citations_register_under_intake_form_key(self) -> None:
        # Walk the four citation-bearing slots on IntakeFormExtraction
        # — chief concern + the four list types — and verify each
        # registers under ("intake_form", field_or_chunk_id).
        chief_citation = _intake_citation("chief_concern")
        demo_citation = _intake_citation("dob")
        med_citation = _intake_citation("med_0")
        allergy_citation = _intake_citation("allergy_0")

        extraction = IntakeFormExtraction(
            document_id=42,
            patient_id=7,
            extraction_confidence=0.9,
            chief_concern="recurring headaches",
            chief_concern_citation=chief_citation,
            demographics=[
                Demographic(field="dob", value="1972-04-12", citation=demo_citation),
            ],
            medications=[
                MedicationEntry(name="metformin", citation=med_citation),
            ],
            allergies=[
                AllergyEntry(substance="penicillin", citation=allergy_citation),
            ],
        )

        state = _starter_state()
        state["extraction_result"] = extraction

        index = build_w2_citation_index(state)

        assert index.size == 4
        assert index.contains("intake_form", "chief_concern")
        assert index.contains("intake_form", "dob")
        assert index.contains("intake_form", "med_0")
        assert index.contains("intake_form", "allergy_0")


# ---------------------------------------------------------------------------
# MR 4 — terminal_node verifies the final assistant message (Task 1.6)
# ---------------------------------------------------------------------------


class TestTerminalNode:
    @pytest.mark.asyncio
    async def test_no_assistant_message_is_noop(self) -> None:
        # If synthesize never ran (e.g. early termination), terminal
        # has nothing to verify. No state mutation beyond the
        # observability ``last_node`` stamp.
        state = _starter_state(user_message="hi")

        update = await terminal_node(state)

        assert update == {"last_node": "terminal"}

    @pytest.mark.asyncio
    async def test_clean_text_passes_through_unchanged(self) -> None:
        # Framing prose without any citations passes the W1 verifier
        # by design — "if you cite, cite truthfully" rather than
        # "every sentence must cite."
        state = _starter_state(user_message="hello")
        state["messages"].append(
            {"role": "assistant", "content": "Hi there. How can I help?"}
        )

        update = await terminal_node(state)

        assert update["messages"][-1]["content"] == "Hi there. How can I help?"
        assert update["messages"][-1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_unverified_claim_replaced_with_rejection_marker(self) -> None:
        # A citation that doesn't resolve in the index must be
        # rejected — this is the trust boundary. The claim text gets
        # replaced with REJECTION_MARKER so the model can't sneak
        # ungrounded clinical assertions past the verifier.
        from agentforge.verifier.streaming_verifier import REJECTION_MARKER

        # State has no evidence and no extraction → empty index.
        state = _starter_state(user_message="A1C target?")
        state["messages"].append(
            {
                "role": "assistant",
                "content": "Target is <7% [guideline #ada-9-1].",
            }
        )

        update = await terminal_node(state)

        assert REJECTION_MARKER in update["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_resolved_citation_keeps_claim_intact(self) -> None:
        # When the citation tag resolves in the index, the claim
        # passes verification and the original text is preserved.
        result = _retrieval_result(chunk_id="ada-9-1")

        state = _starter_state(user_message="A1C target?")
        state["evidence_chunks"] = [result]
        state["messages"].append(
            {
                "role": "assistant",
                "content": "Target is <7% [guideline #ada-9-1].",
            }
        )

        update = await terminal_node(state)

        assert "Target is <7%" in update["messages"][-1]["content"]
        # Citation tag is preserved — verifier doesn't strip it.
        assert "[guideline #ada-9-1]" in update["messages"][-1]["content"]


# ---------------------------------------------------------------------------
# MR 4 — synthesize → terminal end-to-end via build_graph
# ---------------------------------------------------------------------------


class TestSynthesizeTerminalIntegration:
    @pytest.mark.asyncio
    async def test_grounded_answer_survives_terminal_verification(self) -> None:
        # The minimal verified-answer path: state has evidence with a
        # citation, the LLM stub emits an answer that cites it, and
        # terminal_node verifies the citation against the index it
        # builds from state. The verified answer should reach final
        # state intact (citation tag preserved).
        result = _retrieval_result(chunk_id="ada-9-1")
        llm = StubSynthesisLLM(
            response_text="Target is <7% [guideline #ada-9-1]."
        )

        planner = StubPlanner(_empty_followup_plan())
        graph = build_graph(planner, synthesis_llm=llm)
        state = _starter_state(user_message="A1C target?")
        state["evidence_chunks"] = [result]

        final = await graph.ainvoke(state)

        # Last message is the verified assistant answer.
        last = final["messages"][-1]
        assert last["role"] == "assistant"
        assert "Target is <7%" in last["content"]
        assert "[guideline #ada-9-1]" in last["content"]

    @pytest.mark.asyncio
    async def test_ungrounded_citation_blocked_by_terminal(self) -> None:
        # The trust-boundary case: LLM stub emits a citation that
        # doesn't resolve. terminal_node must replace the claim with
        # the rejection marker before it reaches the user.
        from agentforge.verifier.streaming_verifier import REJECTION_MARKER

        llm = StubSynthesisLLM(
            response_text="Target is <7% [guideline #not-a-real-chunk]."
        )

        planner = StubPlanner(_empty_followup_plan())
        graph = build_graph(planner, synthesis_llm=llm)
        # Empty evidence → empty index → unresolved citation.
        final = await graph.ainvoke(_starter_state(user_message="A1C target?"))

        assert REJECTION_MARKER in final["messages"][-1]["content"]
