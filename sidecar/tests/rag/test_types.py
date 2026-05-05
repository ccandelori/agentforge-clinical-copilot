"""Tests for :class:`GuidelineChunk` + :class:`RetrievalResult`.

Light coverage — the dataclasses themselves are mostly inert. The
load-bearing behavior is the ``from_index_entry`` classmethod that
builds a Citation with the right shape, plus the immutability of the
``frozen=True`` dataclasses.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agentforge.rag.types import GuidelineChunk, RetrievalResult
from agentforge.schemas.citation import Citation, SourceType


def _entry_kwargs(**overrides: object) -> dict[str, Any]:
    base = {
        "doc_id": "ada_glycemic_targets",
        "section": "A1c targets for adults",
        "version": "2024-01",
        "chunk_id": "ada_glycemic_targets#0001",
        "text": "Adults with diabetes generally target an A1c <7.0%.",
        "token_count": 12,
        "source_path": "guidelines/ada_glycemic_targets/v2024-01.md",
    }
    base.update(overrides)
    return base


def test_guideline_chunk_from_index_entry_builds_citation_with_correct_shape() -> None:
    chunk = GuidelineChunk.from_index_entry(**_entry_kwargs())

    assert chunk.doc_id == "ada_glycemic_targets"
    cite = chunk.citation
    assert isinstance(cite, Citation)
    assert cite.source_type == SourceType.GUIDELINE
    assert cite.source_id == "ada_glycemic_targets"
    assert cite.page_or_section == "A1c targets for adults"
    assert cite.field_or_chunk_id == "ada_glycemic_targets#0001"
    assert cite.quote_or_value == chunk.text
    # GUIDELINE citations carry no bbox by design (unlike scanned-source
    # types). The Citation validator enforces the absence is fine here.
    assert cite.page_bbox is None


def test_guideline_chunk_is_frozen() -> None:
    chunk = GuidelineChunk.from_index_entry(**_entry_kwargs())
    with pytest.raises(FrozenInstanceError):
        chunk.doc_id = "different_id"  # type: ignore[misc]


def test_retrieval_result_pairs_chunk_with_score() -> None:
    chunk = GuidelineChunk.from_index_entry(**_entry_kwargs())
    result = RetrievalResult(chunk=chunk, score=2.34)

    assert result.chunk is chunk
    assert result.score == pytest.approx(2.34)


def test_retrieval_result_is_frozen() -> None:
    chunk = GuidelineChunk.from_index_entry(**_entry_kwargs())
    result = RetrievalResult(chunk=chunk, score=1.0)
    with pytest.raises(FrozenInstanceError):
        result.score = 99.0  # type: ignore[misc]
