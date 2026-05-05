"""Tests for :class:`Reranker` Protocol + :class:`PassthroughReranker`.

The Protocol is documented; the only shipped implementation in this
MR is the passthrough. Future ML rerankers (CrossEncoder, Cohere)
land in their own MR — when they do, their tests will reuse the
fixtures and Protocol-conformance check below.
"""

from __future__ import annotations

import pytest

from agentforge.rag.reranker import PassthroughReranker, Reranker
from agentforge.rag.types import GuidelineChunk, RetrievalResult


def _chunk(chunk_id: str, text: str = "body") -> GuidelineChunk:
    return GuidelineChunk.from_index_entry(
        doc_id="d", section="s", version="v",
        chunk_id=chunk_id, text=text, token_count=1, source_path="p",
    )


def _result(chunk_id: str, score: float = 0.0) -> RetrievalResult:
    return RetrievalResult(chunk=_chunk(chunk_id), score=score)


def test_passthrough_satisfies_protocol() -> None:
    rer: Reranker = PassthroughReranker()
    assert isinstance(rer, Reranker)


async def test_passthrough_preserves_order_and_truncates_to_top_k() -> None:
    candidates = [_result("a", 1.0), _result("b", 0.5), _result("c", 0.1)]
    rer = PassthroughReranker()
    out = await rer.rerank("anything", candidates, top_k=2)
    assert [r.chunk.chunk_id for r in out] == ["a", "b"]


async def test_passthrough_returns_input_unchanged_when_top_k_exceeds_size() -> None:
    candidates = [_result("a", 1.0), _result("b", 0.5)]
    out = await PassthroughReranker().rerank("q", candidates, top_k=5)
    assert [r.chunk.chunk_id for r in out] == ["a", "b"]


async def test_passthrough_handles_empty_candidates() -> None:
    out = await PassthroughReranker().rerank("q", [], top_k=5)
    assert out == []


async def test_passthrough_rejects_non_positive_top_k() -> None:
    candidates = [_result("a", 1.0)]
    rer = PassthroughReranker()
    with pytest.raises(ValueError, match="top_k"):
        await rer.rerank("q", candidates, top_k=0)
    with pytest.raises(ValueError, match="top_k"):
        await rer.rerank("q", candidates, top_k=-1)


async def test_passthrough_returns_independent_list_not_input_alias() -> None:
    """Mutating the returned list must not affect the input — the
    contract is a value, not a view."""
    candidates = [_result("a"), _result("b"), _result("c")]
    out = await PassthroughReranker().rerank("q", candidates, top_k=3)
    out.clear()
    # The original is untouched.
    assert len(candidates) == 3
