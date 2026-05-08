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


# ---------------------------------------------------------------------------
# 15.3 — reranker selection: COHERE_API_KEY → Cohere, else CrossEncoder,
# explicit force_passthrough → PassthroughReranker for ablation runs.
# ---------------------------------------------------------------------------


class TestSelectReranker:
    """The orchestrator's reranker is picked at app-construction time
    based on environment + ablation flags.

    Three branches, no auto-fallback to Passthrough:

    * ``cohere_api_key`` set → ``CohereReranker``. This is the
      production deployment shape when the operator pays for Cohere.
    * ``cohere_api_key`` empty/None → ``CrossEncoderReranker`` over
      bge-reranker-base. The local default; no per-request cost.
    * ``force_passthrough=True`` → ``PassthroughReranker`` regardless
      of the API key. Used by the eval suite's ablation runs that
      isolate the rerank contribution from the rest of the pipeline.

    Construction failures (e.g. cohere SDK missing) propagate as
    ``ValueError`` from the underlying class — the factory does not
    swallow them. A misconfigured deployment that sets
    ``COHERE_API_KEY`` without installing the SDK should fail loud at
    startup, not silently fall back to a different reranker without
    the operator's knowledge.
    """

    def test_cohere_key_selects_cohere_reranker(self) -> None:
        from agentforge.rag.reranker_factory import select_reranker

        # CohereReranker.__init__ tries to import the cohere SDK; we
        # supply a stub via the ``cohere_factory`` injection point so
        # the test stays free of the optional dependency.
        constructed: list[str] = []

        class _StubCohereReranker:
            def __init__(self, api_key: str) -> None:
                constructed.append(api_key)

            async def rerank(
                self, query: str, candidates: object, *, top_k: int
            ) -> list[object]:
                del query, candidates, top_k
                return []

        reranker = select_reranker(
            cohere_api_key="ck-test",
            cohere_factory=_StubCohereReranker,
        )

        assert isinstance(reranker, _StubCohereReranker)
        assert constructed == ["ck-test"]

    def test_no_cohere_key_selects_cross_encoder_reranker(self) -> None:
        from agentforge.rag.cross_encoder import CrossEncoderReranker
        from agentforge.rag.reranker_factory import select_reranker

        # CrossEncoderReranker takes a CrossEncoder Protocol instance,
        # not the SDK class — we pass a tiny stub so the test doesn't
        # download bge-reranker-base.
        class _StubCrossEncoder:
            def predict(self, pairs: object) -> list[float]:
                del pairs
                return []

        reranker = select_reranker(
            cohere_api_key=None,
            cross_encoder=_StubCrossEncoder(),
        )

        assert isinstance(reranker, CrossEncoderReranker)

    def test_empty_cohere_key_treated_as_no_key(self) -> None:
        # Operators occasionally export an empty COHERE_API_KEY when
        # cycling secrets — that should pick CrossEncoder, not raise
        # CohereReranker's "non-empty api_key" guard at startup.
        from agentforge.rag.cross_encoder import CrossEncoderReranker
        from agentforge.rag.reranker_factory import select_reranker

        class _StubCrossEncoder:
            def predict(self, pairs: object) -> list[float]:
                del pairs
                return []

        reranker = select_reranker(
            cohere_api_key="",
            cross_encoder=_StubCrossEncoder(),
        )

        assert isinstance(reranker, CrossEncoderReranker)

    def test_force_passthrough_overrides_both_branches(self) -> None:
        from agentforge.rag.reranker import PassthroughReranker
        from agentforge.rag.reranker_factory import select_reranker

        class _StubCohereReranker:
            def __init__(self, api_key: str) -> None:
                raise AssertionError(
                    "Cohere should not be constructed under force_passthrough"
                )

        reranker = select_reranker(
            cohere_api_key="ck-test",
            cohere_factory=_StubCohereReranker,
            force_passthrough=True,
        )

        assert isinstance(reranker, PassthroughReranker)


# ---------------------------------------------------------------------------
# 15.4 — node round-trips a real EvidenceRetriever pipeline
# ---------------------------------------------------------------------------


class TestEvidenceNodePipelineWiring:
    """Smoke-test the full BM25 + Dense + RRF + Reranker pipeline through
    the node with stub encoders / corpora. The unit-level
    ``EvidenceRetriever`` tests already cover the pre-filter / merge /
    rerank composition; this case verifies the node hands a real
    pipeline a query and gets back the top_k=5 results without
    re-implementing any pipeline behavior in the node body.
    """

    async def test_node_drives_full_pipeline_top_k_5(self) -> None:
        from collections.abc import Sequence

        import numpy as np
        from numpy.typing import NDArray

        from agentforge.rag.bm25 import BM25Retriever
        from agentforge.rag.dense import DenseRetriever
        from agentforge.rag.evidence_retriever import EvidenceRetriever
        from agentforge.rag.reranker import PassthroughReranker
        from agentforge.rag.rrf import RRFMerger

        # Six chunks (one more than top_k) with orthogonal embeddings so
        # we can predict which chunk each query lands on. The dense
        # encoder is a deterministic table lookup so the test never
        # touches a real model.
        chunks = [
            GuidelineChunk.from_index_entry(
                doc_id="g",
                section="s",
                version="v",
                chunk_id=f"c{i}",
                text=f"text-{i}",
                token_count=2,
                source_path="g.pdf",
            )
            for i in range(6)
        ]

        class _StubEncoder:
            def __init__(self, table: dict[str, list[float]]) -> None:
                self._table = {
                    k: np.array(v, dtype=np.float32) for k, v in table.items()
                }

            def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
                if not texts:
                    dim = next(iter(self._table.values())).shape[0]
                    return np.zeros((0, dim), dtype=np.float32)
                return np.vstack([self._table[t] for t in texts])

        encoder_table: dict[str, list[float]] = {
            f"text-{i}": [1.0 if j == i else 0.0 for j in range(6)]
            for i in range(6)
        }
        # Query vector aligns with chunk c0; BM25 also term-overlaps
        # since the query word appears in c0's text.
        encoder_table["text-0 relevant"] = [
            1.0 if j == 0 else 0.0 for j in range(6)
        ]

        retriever = EvidenceRetriever(
            bm25=BM25Retriever(chunks),
            dense=DenseRetriever(chunks, encoder=_StubEncoder(encoder_table)),
            merger=RRFMerger(),
            reranker=PassthroughReranker(),
        )

        state = _starter_state(query="text-0 relevant")
        update = await evidence_retriever_node(state, retriever)

        results = update["evidence_chunks"]
        # The node defaults to top_k=5 — the pipeline must return at
        # most that many results regardless of corpus size.
        assert 1 <= len(results) <= 5
        # The top result must be c0 — both retrievers concur on it.
        assert results[0].chunk.chunk_id == "c0"

    async def test_node_round_trips_citation_through_pipeline(self) -> None:
        # Smaller variant: confirm the GUIDELINE citation survives the
        # full pipeline (no chunk → result rewrite drops it). This is
        # the contract piece that downstream `[guideline #chunk_id]`
        # tag resolution depends on.
        from collections.abc import Sequence

        import numpy as np
        from numpy.typing import NDArray

        from agentforge.rag.bm25 import BM25Retriever
        from agentforge.rag.dense import DenseRetriever
        from agentforge.rag.evidence_retriever import EvidenceRetriever
        from agentforge.rag.reranker import PassthroughReranker
        from agentforge.rag.rrf import RRFMerger

        chunks = [
            GuidelineChunk.from_index_entry(
                doc_id="ada-2024",
                section="9.1",
                version="2024",
                chunk_id="ada-9-1#0",
                text="A1C target",
                token_count=2,
                source_path="ada.pdf",
            )
        ]

        class _StubEncoder:
            def encode(self, texts: Sequence[str]) -> NDArray[np.float32]:
                if not texts:
                    return np.zeros((0, 1), dtype=np.float32)
                return np.ones((len(texts), 1), dtype=np.float32)

        retriever = EvidenceRetriever(
            bm25=BM25Retriever(chunks),
            dense=DenseRetriever(chunks, encoder=_StubEncoder()),
            merger=RRFMerger(),
            reranker=PassthroughReranker(),
        )

        state = _starter_state(query="A1C target")
        update = await evidence_retriever_node(state, retriever)

        result = update["evidence_chunks"][0]
        cite = result.chunk.citation
        assert cite.source_type is SourceType.GUIDELINE
        assert cite.source_id == "ada-2024"
        assert cite.page_or_section == "9.1"
        assert cite.field_or_chunk_id == "ada-9-1#0"
        assert cite.quote_or_value == "A1C target"
