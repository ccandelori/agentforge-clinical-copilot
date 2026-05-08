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
