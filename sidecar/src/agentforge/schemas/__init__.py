"""W2 Pydantic schemas — citation contract and extraction shapes.

The schemas in this package are the contract between extraction
(vision tool output), retrieval (RAG hits), the verifier (claim
grounding), and the UI overlay (page_bbox positioning). Adding a field
to a model is a coordinated change across all four layers.

See W2_ARCHITECTURE.md §2.2.
"""

from agentforge.schemas.citation import (
    SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR,
    Citation,
    PageBBox,
    SourceType,
)

__all__ = [
    "SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR",
    "Citation",
    "PageBBox",
    "SourceType",
]
