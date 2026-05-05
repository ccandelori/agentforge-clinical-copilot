"""Validation tests for the AgentForge clinical-guideline corpus.

These are *corpus* tests, not retriever tests — they catch breakage in
``data/guidelines/index.json`` itself (stale chunk shape, missing
metadata, miscounted chunk count). The retriever's own behavior is
demoed by ``scripts/retrieval_demo.py``; production retrieval
correctness gets locked in Task 15.

Test scope mirrors the Task 10 test strategy:

1. Every guideline file is valid UTF-8.
2. ``index.json`` parses cleanly.
3. Chunk count is non-trivial (we have at least one chunk per doc).
4. Each chunk carries the required metadata fields.
5. Token counts are non-negative integers and never zero
   (an empty chunk would be a chunker bug).

The chunk-count assertion uses ``>=`` rather than ``==`` because the
demo corpus is intentionally smaller than the production target
(~600 chunks); locking the exact count would make every guideline
edit fail the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "data" / "guidelines"
INDEX_PATH = CORPUS_ROOT / "index.json"

REQUIRED_CHUNK_FIELDS = (
    "doc_id",
    "section",
    "version",
    "chunk_id",
    "text",
    "token_count",
    "source_path",
)


@pytest.fixture(scope="module")
def index_data() -> dict:
    if not INDEX_PATH.exists():
        pytest.skip(
            "index.json not built — run "
            "`uv run python scripts/chunk_guidelines.py` first."
        )
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def test_every_guideline_file_is_valid_utf8() -> None:
    """Test Strategy #1. A non-UTF-8 file would crash the chunker
    silently in production; lock the encoding contract here."""
    md_files = list(CORPUS_ROOT.rglob("*.md"))
    assert md_files, "no markdown files found in corpus"
    for md in md_files:
        # decode raises UnicodeDecodeError on bad bytes
        md.read_text(encoding="utf-8")


def test_index_json_parses_cleanly(index_data: dict) -> None:
    """Test Strategy #2."""
    assert isinstance(index_data, dict)
    assert "chunks" in index_data
    assert isinstance(index_data["chunks"], list)


def test_index_reports_doc_and_chunk_counts(index_data: dict) -> None:
    md_files = [
        p for p in CORPUS_ROOT.rglob("*.md") if p.name != "NOTICE.md"
    ]
    assert index_data.get("doc_count") == len(md_files)
    assert index_data.get("chunk_count") == len(index_data["chunks"])


def test_chunk_count_is_at_least_one_per_doc(index_data: dict) -> None:
    """Test Strategy #3 (relaxed). The demo corpus is smaller than the
    600-chunk production target; assert the floor instead of the
    target so guideline edits don't fail the test."""
    md_files = [
        p for p in CORPUS_ROOT.rglob("*.md") if p.name != "NOTICE.md"
    ]
    assert len(index_data["chunks"]) >= len(md_files), (
        "expected at least one chunk per markdown document"
    )


def test_every_chunk_has_required_metadata(index_data: dict) -> None:
    """Test Strategy #4."""
    for i, chunk in enumerate(index_data["chunks"]):
        missing = [f for f in REQUIRED_CHUNK_FIELDS if f not in chunk]
        assert not missing, (
            f"chunk #{i} ({chunk.get('chunk_id', '?')}) missing fields: {missing}"
        )


def test_chunk_ids_are_globally_unique(index_data: dict) -> None:
    """The retriever uses chunk_id as the citation field_or_chunk_id;
    duplicate IDs would silently merge results in the overlay UI."""
    ids = [c["chunk_id"] for c in index_data["chunks"]]
    assert len(ids) == len(set(ids)), "chunk_id collisions detected"


def test_token_counts_are_positive_integers(index_data: dict) -> None:
    """Test Strategy #5. Zero-token chunks would be a packing bug;
    negative counts a serialization bug."""
    for chunk in index_data["chunks"]:
        tc = chunk["token_count"]
        assert isinstance(tc, int)
        assert tc > 0, f"chunk {chunk['chunk_id']} has token_count={tc}"


def test_source_paths_resolve_under_corpus_root(index_data: dict) -> None:
    """Each chunk's source_path is relative to data/guidelines/ and
    must point to an existing file. Catches stale index.json after a
    file rename."""
    for chunk in index_data["chunks"]:
        full = CORPUS_ROOT / chunk["source_path"]
        assert full.exists(), f"missing source file: {chunk['source_path']}"


def test_doc_ids_match_a_known_topic(index_data: dict) -> None:
    """Sanity-lock the topic taxonomy. Adding a new topic dir is a
    coordinated change with the synthesizer's prompts (which expect
    'guideline' citations to come from a stable set of topics)."""
    expected_topics = {"diabetes", "hypertension", "lipids", "renal", "labs"}
    seen_topics = set()
    for chunk in index_data["chunks"]:
        topic = chunk["source_path"].split("/", 1)[0]
        seen_topics.add(topic)
    unknown = seen_topics - expected_topics
    assert not unknown, f"unknown topic dirs in corpus: {unknown}"
