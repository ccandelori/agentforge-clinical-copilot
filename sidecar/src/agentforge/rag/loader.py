"""Load a chunked guideline corpus from disk into :class:`GuidelineChunk` objects.

The chunker (:mod:`scripts.chunk_guidelines`) emits an ``index.json``
with one entry per chunk; this module is the inverse — it reads that
file and produces the typed chunks the retrievers consume. Pulled
out into its own module so the demo, the orchestrator, and the
LangGraph evidence-retriever node (Task 15) all consume the same
loader rather than each duplicating the JSON shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentforge.rag.types import GuidelineChunk


def load_corpus(index_path: Path) -> list[GuidelineChunk]:
    """Read ``index.json`` and return a list of typed chunks.

    Raises :class:`FileNotFoundError` if the file is missing and
    :class:`KeyError` if any chunk entry is missing a required field
    — those failure modes are caller-relevant (the caller can suggest
    re-running the chunker), not retriever-internal noise.
    """
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    chunks: list[GuidelineChunk] = []
    for entry in raw.get("chunks", []):
        chunks.append(
            GuidelineChunk.from_index_entry(
                doc_id=entry["doc_id"],
                section=entry["section"],
                version=entry["version"],
                chunk_id=entry["chunk_id"],
                text=entry["text"],
                token_count=int(entry["token_count"]),
                source_path=entry["source_path"],
            )
        )
    return chunks
