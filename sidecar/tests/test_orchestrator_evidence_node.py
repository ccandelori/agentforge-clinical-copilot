"""Tests for the W2 ``evidence_retriever_node`` (Task 15).

These tests pin the node's external contract — the ``Citation`` shape
on every retrieved chunk and the reranker-selection / observability
hooks the production wiring needs. The node body itself lives in
:mod:`agentforge.orchestrator.graph`; this file owns the suite that
travels with the wiring work in Task 15.

Splitting the suite out of ``test_orchestrator_graph.py`` keeps the
multi-MR graph file focused on graph-shape concerns; the evidence
node's contract (citation shape, reranker factory, langfuse trace
emission) is its own surface and gets its own file.
"""

from __future__ import annotations

from agentforge.orchestrator.graph import (
    HANDOFF_START_NODE,
    AgentState,
    RouteDecision,
    evidence_retriever_node,
)
from agentforge.rag.types import GuidelineChunk, RetrievalResult
from agentforge.schemas.citation import Citation, SourceType


def _starter_state(query: str = "") -> AgentState:
    return AgentState(
        messages=[{"role": "user", "content": "hello"}],
        tool_results={},
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
        document_id=None,
        patient_id=None,
        pdf_pages=[],
        query=query,
        langfuse_trace=None,
        last_node=HANDOFF_START_NODE,
    )


def _retrieval_result(
    *,
    chunk_id: str = "ada-9-1#0",
    doc_id: str = "ada-2024",
    section: str = "9.1",
    text: str = "A1C target for most adults with diabetes is <7%.",
    score: float = 0.9,
) -> RetrievalResult:
    chunk = GuidelineChunk.from_index_entry(
        doc_id=doc_id,
        section=section,
        version="2024",
        chunk_id=chunk_id,
        text=text,
        token_count=12,
        source_path=f"{doc_id}.pdf",
    )
    return RetrievalResult(chunk=chunk, score=score)


class _StubRetriever:
    """Minimal :class:`_EvidenceRetrieverLike` for the node's contract tests."""

    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results
        self.calls: list[dict[str, object]] = []

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]:
        self.calls.append({"query": query, "top_k": top_k})
        return list(self._results)


# ---------------------------------------------------------------------------
# 15.1 — pin the Citation contract on each retrieved chunk
# ---------------------------------------------------------------------------


class TestEvidenceNodeCitationContract:
    """The node's output is a list of ``RetrievalResult`` whose
    ``chunk.citation`` carries the W2 GUIDELINE shape:

    * ``source_type == SourceType.GUIDELINE``
    * ``source_id`` is the chunk's ``doc_id``
    * ``page_or_section`` is the chunk's ``section``
    * ``field_or_chunk_id`` is the chunk's ``chunk_id``
    * ``quote_or_value`` is the chunk's full ``text``
    * ``page_bbox is None`` (GUIDELINE citations don't carry bboxes)

    Pinning this here so a future refactor of either ``GuidelineChunk``
    or the node can't silently drift the contract that
    ``build_w2_citation_index`` and the synthesizer's ``[guideline #id]``
    grammar both depend on.
    """

    async def test_node_emits_chunks_with_guideline_citation(self) -> None:
        retriever = _StubRetriever(
            [
                _retrieval_result(
                    chunk_id="ada-9-1#0",
                    doc_id="ada-2024",
                    section="9.1 Glycemic Targets",
                    text="A1C target for most adults with diabetes is <7%.",
                ),
                _retrieval_result(
                    chunk_id="ada-9-1#1",
                    doc_id="ada-2024",
                    section="9.1 Glycemic Targets",
                    text="A more stringent target may be appropriate for some.",
                    score=0.7,
                ),
            ]
        )

        state = _starter_state(query="A1C target adult diabetes")
        update = await evidence_retriever_node(state, retriever)

        chunks = update["evidence_chunks"]
        assert len(chunks) == 2

        for result in chunks:
            cite: Citation = result.chunk.citation
            assert cite.source_type is SourceType.GUIDELINE
            assert cite.source_id == result.chunk.doc_id
            assert cite.page_or_section == result.chunk.section
            assert cite.field_or_chunk_id == result.chunk.chunk_id
            assert cite.quote_or_value == result.chunk.text
            assert cite.page_bbox is None

    async def test_node_passes_top_k_5_to_retriever(self) -> None:
        # The W2 spec pins top_k=5 — verify the node honors that
        # default. A drift here would silently change the synthesizer
        # context budget without an architectural review.
        retriever = _StubRetriever([_retrieval_result()])

        state = _starter_state(query="anything")
        await evidence_retriever_node(state, retriever)

        assert retriever.calls == [{"query": "anything", "top_k": 5}]

    async def test_node_stamps_last_node_for_handoff_span(self) -> None:
        retriever = _StubRetriever([_retrieval_result()])

        state = _starter_state(query="anything")
        update = await evidence_retriever_node(state, retriever)

        assert update["last_node"] == RouteDecision.EVIDENCE_RETRIEVER.value


# ---------------------------------------------------------------------------
# 15.2 — node signature accepts an optional langfuse client + trace
# ---------------------------------------------------------------------------


class TestEvidenceNodeSignature:
    """The node's keyword-only ``langfuse`` parameter is the seam for
    Task 15.5's ``retrieval_hits`` span. Pinning the signature here so
    later changes to the span payload don't accidentally make
    ``langfuse`` positional or required.

    No-op when ``langfuse`` is None (the production NullLangfuseClient
    path is wired this way) or when the trace handle is absent — the
    node still returns the same chunks, the only difference is whether
    a span is emitted. The span payload itself is asserted in the 15.5
    suite below.
    """

    async def test_node_accepts_langfuse_keyword_and_runs_without_trace(
        self,
    ) -> None:
        from unittest.mock import MagicMock

        retriever = _StubRetriever([_retrieval_result()])
        langfuse = MagicMock()

        state = _starter_state(query="anything")
        # langfuse_trace stays None — the node must not call the
        # client when there's no trace to attach a span to.
        update = await evidence_retriever_node(
            state, retriever, langfuse=langfuse
        )

        assert update["evidence_chunks"]
        # Symmetric with the supervisor's handoff-span guard: with no
        # trace, no span is emitted (otherwise the Null client gets a
        # fake trace and the dashboard's trace_id stays None).
        assert not langfuse.method_calls

    async def test_node_no_ops_langfuse_when_client_is_none(self) -> None:
        # Default: callers who haven't wired langfuse keep the previous
        # behavior — the node's ``langfuse`` kwarg defaults to None and
        # the function executes without the optional client.
        retriever = _StubRetriever([_retrieval_result()])

        state = _starter_state(query="anything")
        update = await evidence_retriever_node(state, retriever, langfuse=None)

        assert update["evidence_chunks"]
