"""End-to-end hybrid RAG pipeline: BM25 + Dense → RRF → Reranker.

The :class:`EvidenceRetriever` composes the four shipped components
into a single ``retrieve(query, top_k)`` surface that the LangGraph
evidence-retriever node (Task 15) calls. The composition is fixed —
hybrid retrieval has a known-good shape and exposing knobs at this
level would push complexity into the orchestrator without buying
flexibility (configuration via DI of the components is the right
seam).

Two stages:

  1. **Pre-filter**: BM25 + Dense each return their top-N
     candidates (default N = top_k * 4). The two ranked lists are
     fused via RRF, deduplicated by chunk_id, and truncated to
     ``rerank_input_size`` candidates.

  2. **Rerank**: the reranker scores the pre-filter's candidates
     jointly with the query and returns the final top-``top_k``.

The pre-filter's per-retriever top-N must be wider than the final
``top_k`` to give the reranker something to reorder. The default
``rerank_input_size = 4 * top_k`` is a reasonable balance between
recall (more candidates = better odds the relevant chunk is in the
pool) and rerank latency (fewer candidates = fewer cross-encoder
forward passes).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agentforge.rag.bm25 import BM25Retriever
from agentforge.rag.dense import DenseRetriever
from agentforge.rag.reranker import Reranker
from agentforge.rag.rrf import RRFMerger
from agentforge.rag.types import RetrievalResult


@dataclass(frozen=True)
class RetrievalConfig:
    """Top-level pipeline knobs. Frozen so each EvidenceRetriever
    has a stable configuration; rebuild a new instance to tune."""

    rerank_multiplier: int = 4
    """Pre-filter pool size = top_k * this. Default 4 → top_k=5
    fetches 20 candidates from each retriever before RRF/rerank."""


@dataclass(frozen=True)
class RetrievalStats:
    """Per-stage counts emitted alongside the final reranked results.

    The node uses these to populate the Langfuse ``retrieval_hits``
    span (W2_ARCHITECTURE.md §7) so dashboards can roll up per-stage
    contribution without re-running the pipeline. The counts are
    *not* a substitute for trace-level latency — they answer "did
    BM25 contribute?" / "did the reranker actually pick from the
    pool?", not "how long did dense take?".

    All three are non-negative integers bounded by the corpus size +
    rerank pool. Treat them as ordinal — the absolute counts depend
    on ``rerank_multiplier``.
    """

    results: list[RetrievalResult]
    """The final reranked top-``top_k`` slice — same payload as
    :meth:`EvidenceRetriever.retrieve` returns."""

    bm25_count: int
    """How many candidates BM25 contributed to the pre-filter pool."""

    dense_count: int
    """How many candidates the dense retriever contributed."""

    post_rerank_count: int
    """How many candidates survived to the final result. Equals
    ``len(results)``; surfaced as a separate field so dashboards can
    consume the three counts uniformly without unwrapping the list."""


class EvidenceRetriever:
    """Hybrid BM25 + Dense + RRF + Reranker pipeline.

    All four collaborators are injected; the orchestrator wires up
    the right combination based on deployment config (e.g. a
    cohere-equipped deployment swaps in :class:`CohereReranker`,
    a stripped-down one uses :class:`PassthroughReranker`).
    """

    def __init__(
        self,
        bm25: BM25Retriever,
        dense: DenseRetriever,
        merger: RRFMerger,
        reranker: Reranker,
        *,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._bm25 = bm25
        self._dense = dense
        self._merger = merger
        self._reranker = reranker
        self._config = config or RetrievalConfig()

        # The two retrievers should agree on corpus size — different
        # corpora through a single EvidenceRetriever would mean
        # citations that don't round-trip across retrievers.
        if bm25.corpus_size != dense.corpus_size:
            raise ValueError(
                f"BM25 corpus size ({bm25.corpus_size}) does not match "
                f"Dense corpus size ({dense.corpus_size}); the retriever "
                f"requires a single shared corpus"
            )

    @property
    def corpus_size(self) -> int:
        return self._bm25.corpus_size

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievalResult]:
        """Return the top-``top_k`` reranked guideline chunks for ``query``.

        Returns ``[]`` when the corpus is empty or BM25+Dense produce
        no candidates after the pre-filter. The reranker is never
        called with an empty candidate list.

        Thin wrapper over :meth:`retrieve_with_stats` for callers that
        don't need per-stage counts. Kept on the surface because every
        existing caller (W1 path, the existing graph node fallback)
        speaks this shape and there's no benefit forcing them to
        unwrap the stats DTO.
        """
        stats = await self.retrieve_with_stats(query, top_k=top_k)
        return stats.results

    async def retrieve_with_stats(
        self, query: str, *, top_k: int = 5
    ) -> RetrievalStats:
        """Retrieve top-``top_k`` chunks plus per-stage counts.

        Same pipeline as :meth:`retrieve`; returns the counts the
        Langfuse ``retrieval_hits`` span consumes (BM25 contribution,
        dense contribution, post-rerank survivors). The counts come
        from the components' actual output, not the rerank pool size,
        so a BM25 retriever that drops zero-score hits surfaces fewer
        contributions than the configured pool — which is what we
        want to surface on the dashboard.
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be positive; got {top_k}")
        if self.corpus_size == 0:
            return RetrievalStats(
                results=[],
                bm25_count=0,
                dense_count=0,
                post_rerank_count=0,
            )

        rerank_input_size = top_k * self._config.rerank_multiplier
        # BM25 is sync (CPU-bound, sub-millisecond); Dense is sync but
        # behind an encoder call. Run them in a thread pool so the
        # event loop stays responsive — and the small parallelism win
        # adds up across many queries.
        bm25_results, dense_results = await asyncio.gather(
            asyncio.to_thread(self._bm25.top_k, query, rerank_input_size),
            asyncio.to_thread(self._dense.top_k, query, rerank_input_size),
        )

        merged = self._merger.merge(
            [bm25_results, dense_results],
            top_k=rerank_input_size,
        )
        if not merged:
            return RetrievalStats(
                results=[],
                bm25_count=len(bm25_results),
                dense_count=len(dense_results),
                post_rerank_count=0,
            )

        results = await self._reranker.rerank(query, merged, top_k=top_k)
        return RetrievalStats(
            results=results,
            bm25_count=len(bm25_results),
            dense_count=len(dense_results),
            post_rerank_count=len(results),
        )
