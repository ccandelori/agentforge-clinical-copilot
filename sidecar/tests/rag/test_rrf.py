"""Tests for :class:`RRFMerger`.

The merger's contract is rank-only: it ignores the input score
magnitudes and re-emits a fused ordering using ``1/(k+rank)`` summed
across input lists. Tests verify the math, the dedup-by-chunk_id
invariant, the truncation rule, and the empty-input edge cases.
"""

from __future__ import annotations

import pytest

from agentforge.rag.rrf import DEFAULT_RRF_K, RRFMerger
from agentforge.rag.types import GuidelineChunk, RetrievalResult


def _chunk(chunk_id: str) -> GuidelineChunk:
    return GuidelineChunk.from_index_entry(
        doc_id="test_doc",
        section="test",
        version="2024-01",
        chunk_id=chunk_id,
        text=f"chunk body {chunk_id}",
        token_count=3,
        source_path="test.md",
    )


def _result(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(chunk=_chunk(chunk_id), score=score)


def test_merger_default_k_matches_canonical_constant() -> None:
    merger = RRFMerger()
    assert merger.k == DEFAULT_RRF_K == 60


def test_merger_rejects_non_positive_top_k() -> None:
    merger = RRFMerger()
    with pytest.raises(ValueError, match="top_k"):
        merger.merge([[]], top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        merger.merge([[]], top_k=-1)


def test_merger_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="RRF k"):
        RRFMerger(k=0).merge([[]], top_k=5)


def test_merger_handles_all_empty_inputs() -> None:
    merger = RRFMerger()
    assert merger.merge([], top_k=5) == []
    assert merger.merge([[], []], top_k=5) == []


def test_merger_fuses_two_lists_by_summing_reciprocal_ranks() -> None:
    """When the same chunk appears at rank 1 in both lists, its
    merged score is 2 / (k + 1). When it appears at rank 1 in one
    only, its merged score is 1 / (k + 1)."""
    bm25 = [_result("a", 5.0), _result("b", 3.0)]
    dense = [_result("a", 0.9), _result("c", 0.7)]

    merger = RRFMerger(k=60)
    fused = merger.merge([bm25, dense], top_k=5)

    by_id = {r.chunk.chunk_id: r.score for r in fused}
    # 'a' is rank 1 in both → 1/61 + 1/61
    assert by_id["a"] == pytest.approx(2 / 61)
    # 'b' is rank 2 in bm25 only → 1/62
    assert by_id["b"] == pytest.approx(1 / 62)
    # 'c' is rank 2 in dense only → 1/62
    assert by_id["c"] == pytest.approx(1 / 62)


def test_merger_dedupes_by_chunk_id() -> None:
    """Same chunk surfacing twice across the input lists must produce
    one merged entry, not two."""
    bm25 = [_result("a", 9.0)]
    dense = [_result("a", 0.5)]
    fused = RRFMerger().merge([bm25, dense], top_k=10)
    assert len(fused) == 1
    assert fused[0].chunk.chunk_id == "a"


def test_merger_orders_by_summed_score_descending() -> None:
    bm25 = [_result("alpha", 1.0), _result("beta", 0.5), _result("gamma", 0.1)]
    dense = [_result("beta", 0.9), _result("alpha", 0.8), _result("delta", 0.3)]

    fused = RRFMerger(k=60).merge([bm25, dense], top_k=10)
    # alpha + beta both appear in both lists; gamma + delta only one.
    # Merged scores:
    #   alpha:  1/61 + 1/62  (rank 1 + rank 2)
    #   beta:   1/62 + 1/61  (rank 2 + rank 1)  → equal to alpha
    #   gamma:  1/63
    #   delta:  1/63
    chunk_ids = [r.chunk.chunk_id for r in fused]
    # Top two are {alpha, beta}; tie-break is dict-insertion order.
    assert set(chunk_ids[:2]) == {"alpha", "beta"}
    assert set(chunk_ids[2:]) == {"gamma", "delta"}


def test_merger_truncates_to_top_k() -> None:
    inputs = [[_result(f"c{i}", float(20 - i)) for i in range(20)]]
    fused = RRFMerger().merge(inputs, top_k=3)
    assert len(fused) == 3


def test_merger_input_score_does_not_affect_merged_score() -> None:
    """The merger's contract: only ranks matter. A chunk with a wildly
    higher input score but lower rank must NOT outrank a higher-ranked
    chunk."""
    bm25 = [_result("rank2", 1000.0), _result("rank1", 0.001)]
    # Rank order is the position in the input list. rank2 is at index 0
    # (rank 1 by RRF math), rank1 is at index 1. Names confusing
    # intentionally: input score doesn't determine RRF rank.
    fused = RRFMerger().merge([bm25], top_k=2)
    assert fused[0].chunk.chunk_id == "rank2"  # rank 1 in input → top
    assert fused[1].chunk.chunk_id == "rank1"


def test_merger_emits_rrf_score_not_input_score() -> None:
    """The output score is the RRF weight, not the input score. This
    is part of the contract — the merger is the boundary between
    component score-spaces."""
    bm25 = [_result("a", 99999.0)]
    fused = RRFMerger(k=60).merge([bm25], top_k=1)
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[0].score < 1.0  # never the input's 99999
