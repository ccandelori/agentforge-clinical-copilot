"""Generic envelope DTOs every tool returns to the orchestrator.

Tools never raise to the orchestrator: they wrap their domain payload in a
``ToolResult`` along with metadata (provenance, freshness, redaction audit)
that the verifier and synthesis layers consume without having to parse the
payload itself. See ARCHITECTURE.md S4.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ToolResultMetadata(BaseModel):
    """Observability + audit data attached to every ``ToolResult``."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    fetched_at: datetime  # UTC; when the data was retrieved
    data_freshness_seconds: int  # cache TTL or "as-of" delta of the source data
    source: str  # e.g. "openemr.demographics", "openemr.problem_list"
    redaction_applied: bool = False  # true if sensitivity policy redacted any fields
    redacted_fields: tuple[str, ...] = ()  # which fields were dropped


class ToolResult[PayloadT: BaseModel](BaseModel):
    """Generic envelope every tool returns to the orchestrator.

    ``payload`` is the typed domain object; ``metadata`` is observability +
    audit data the verifier and synthesis layers consume without needing to
    parse the payload.
    """

    model_config = ConfigDict(frozen=True)

    metadata: ToolResultMetadata
    payload: PayloadT
