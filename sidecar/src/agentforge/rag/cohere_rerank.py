"""Cohere-backed reranker (opt-in).

The Cohere rerank API is hosted, fast (~50-100ms), and stronger than
local cross-encoders on technical text. It costs per-call though, so
this reranker is **opt-in**: deployments without a ``COHERE_API_KEY``
should fall back to :class:`CrossEncoderReranker` or
:class:`PassthroughReranker`.

The ``cohere`` SDK is an optional dependency (installed via
``pip install agentforge[cohere]``); we lazy-import inside
``__init__`` so a sidecar without the package can still ``from
agentforge.rag import ...`` without an ImportError fallout. The
import error becomes the runtime ValueError "cohere is not installed"
instead of a startup-time crash.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from agentforge.rag.types import RetrievalResult

# v3.5 is the production model as of 2026-05; Cohere keeps the v2.0
# model available for cost-sensitive deployments. The v3.5 docs note
# better technical-text relevance, which is what we want for
# guideline retrieval.
DEFAULT_COHERE_MODEL = "rerank-english-v3.5"


class CohereReranker:
    """Async reranker calling the Cohere rerank API.

    The Cohere SDK is lazy-imported; missing-module is converted to a
    clear runtime ValueError so an unconfigured deployment fails with
    a useful message instead of a cryptic ImportError elsewhere.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_COHERE_MODEL,
    ) -> None:
        if not api_key:
            raise ValueError(
                "CohereReranker requires a non-empty api_key; "
                "supply COHERE_API_KEY or pick PassthroughReranker"
            )
        try:
            import cohere
        except ImportError as exc:
            raise ValueError(
                "cohere SDK is not installed. Install with "
                "`pip install agentforge[cohere]` (or "
                "`uv sync --extra cohere`) to use CohereReranker."
            ) from exc

        # AsyncClient — the rerank call returns a coroutine the SDK
        # awaits. This keeps the orchestrator's event loop free
        # while the network roundtrip happens.
        self._client = cohere.AsyncClient(api_key=api_key)
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int,
    ) -> list[RetrievalResult]:
        if top_k <= 0:
            raise ValueError(f"top_k must be positive; got {top_k}")
        if not candidates:
            return []

        documents = [c.chunk.text for c in candidates]
        response = await self._client.rerank(
            model=self._model,
            query=query,
            documents=documents,
            top_n=min(top_k, len(candidates)),
        )

        # Cohere returns RerankResultsResponseResult objects with
        # ``index`` (into our `documents` list) and ``relevance_score``.
        # Re-emit with the Cohere score replacing the input score.
        results: list[RetrievalResult] = []
        for hit in response.results:
            idx = int(hit.index)
            if idx < 0 or idx >= len(candidates):
                # Defensive: shouldn't happen with the SDK in normal
                # operation, but the runtime error here is much
                # clearer than an IndexError downstream.
                raise RuntimeError(
                    f"Cohere returned out-of-range index {idx} for "
                    f"{len(candidates)} candidates"
                )
            results.append(
                RetrievalResult(
                    chunk=candidates[idx].chunk,
                    score=float(hit.relevance_score),
                )
            )
        return results

    async def aclose(self) -> None:
        """Cohere's AsyncClient holds an httpx pool; close on shutdown.

        Wiring this into the FastAPI lifespan is the orchestrator's
        responsibility; the reranker just exposes the close hook.
        """
        # The SDK's AsyncClient method may be different across versions;
        # call it via getattr to keep this reranker working with both.
        close_fn = getattr(self._client, "close", None) or getattr(
            self._client, "aclose", None
        )
        if close_fn is None:
            return
        result = close_fn()
        if asyncio.iscoroutine(result):
            await result
