"""Build-time gate: assert that the HuggingFace model cache is populated.

Run during ``docker build`` (final builder-stage step) so a missing or
corrupted cache fails the build, not the first production request. The
script forces ``HF_HUB_OFFLINE=1`` for its own subprocess so any attempt
to fetch from huggingface.co raises instead of silently re-downloading.

Models verified:

* ``sentence-transformers/all-MiniLM-L6-v2`` — DenseRetriever embeddings.
* ``BAAI/bge-reranker-base`` — CrossEncoderReranker.

Cohere reranker is intentionally not pre-baked (network-gated by env).
"""

from __future__ import annotations

import os
import sys


def _force_offline() -> None:
    """Make HuggingFace clients refuse network so the cache must satisfy loads."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def main() -> int:
    _force_offline()

    from sentence_transformers import CrossEncoder, SentenceTransformer

    SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    CrossEncoder("BAAI/bge-reranker-base")
    print("model cache verified offline: MiniLM-L6-v2 + bge-reranker-base")
    return 0


if __name__ == "__main__":
    sys.exit(main())
