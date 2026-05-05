"""Reciprocal-rank fusion (RRF) for hybrid BM25 + dense retrieval.

RRF is the standard way to merge ranked lists from heterogeneous
retrievers without trying to calibrate their score spaces. For each
candidate, the merged score is

    sum over retrievers r of   1 / (k + rank_r(candidate))

where ``rank_r`` is 1-indexed and missing-from-retriever-r contributes
0. The constant ``k`` (typically 60) damps the influence of very-low
ranks so a single retriever's deep-tail hit doesn't dominate the
merged ordering.

The merger keys on ``GuidelineChunk.chunk_id`` for deduplication —
the same chunk surfaced by both retrievers produces one merged entry,
not two. The ``citation`` attached to the surviving entry is the
chunk's stable Citation; both retrievers should be returning chunks
built from the same index so this is invariant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agentforge.rag.types import RetrievalResult

# k=60 is the canonical RRF constant from Cormack et al. 2009. Lower
# ``k`` weights top ranks more aggressively; higher ``k`` flattens
# the list. 60 is well-established and not a project-specific
# tuning knob worth carrying in config.
DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class RRFMerger:
    """Merge two or more ranked retrieval lists via reciprocal rank fusion.

    Stateless after construction; safe to share across queries.
    Frozen dataclass so the ``k`` constant is locked at construction —
    callers that want to experiment with k should build a new merger
    rather than mutating a shared one.
    """

    k: int = DEFAULT_RRF_K

    def merge(
        self,
        ranked_lists: Sequence[Sequence[RetrievalResult]],
        *,
        top_k: int,
    ) -> list[RetrievalResult]:
        """Merge ranked lists into a single top-``top_k`` ordering.

        The score on each output :class:`RetrievalResult` is the
        merged RRF weight (sum of 1/(k+rank) across input lists where
        the chunk appeared). It's NOT comparable to any input list's
        raw scores — the merger is the boundary between component
        score-spaces and the unified ranking the orchestrator sees.

        Empty input lists are tolerated; an all-empty input returns
        ``[]``.
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be positive; got {top_k}")
        if self.k <= 0:
            raise ValueError(f"RRF k must be positive; got {self.k}")

        # Map chunk_id → (representative_chunk, summed_rrf_score).
        # The representative is the first occurrence's chunk — both
        # retrievers' results for the same chunk_id should carry the
        # same citation and text, so picking the first is fine.
        merged: dict[str, tuple[RetrievalResult, float]] = {}

        for ranked in ranked_lists:
            for rank_index, result in enumerate(ranked):
                rank_one_based = rank_index + 1
                weight = 1.0 / (self.k + rank_one_based)
                key = result.chunk.chunk_id
                if key in merged:
                    existing_result, existing_score = merged[key]
                    merged[key] = (existing_result, existing_score + weight)
                else:
                    merged[key] = (result, weight)

        # Re-emit as RetrievalResult with the RRF-merged score in
        # place of whatever the input score was. Sort descending by
        # score, then truncate to top_k.
        fused = [
            RetrievalResult(chunk=result.chunk, score=score)
            for result, score in merged.values()
        ]
        fused.sort(key=lambda r: r.score, reverse=True)
        return fused[:top_k]
