"""Schema + fetcher for the ``get_recent_labs`` tool.

Unlike demographics/problems/medications, this tool *does* take an
optional model-supplied parameter (``since_days``) so the agent can
narrow a "show me last week's labs" question without re-fetching the
whole 90-day window. patient_id is still bound at the request level by
the orchestrator — the JWT carries it; the model never sees it. See
ARCHITECTURE.md S4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as _Date
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class LabResultItem(BaseModel):
    """One analyte (single lab measurement) flattened across the
    procedure_order -> procedure_report -> procedure_result join.

    A single order can produce multiple reports and each report can have
    several analytes; the agent reasons better over a flat list of
    analytes than over a nested order/report tree, so we flatten in PHP
    and deliver the analyte rows directly.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    order_id: int
    report_id: int
    test_code: str | None = None
    test_name: str | None = None
    value: str | None = None
    units: str | None = None
    reference_range: str | None = None
    abnormal: str | None = None  # 'no', 'yes', 'high', 'low' (lab-supplied)
    # Field name 'date' shadows the imported `date` type at class-body
    # eval time; aliasing the type as `_Date` lets us keep the wire field
    # name 'date' while still annotating it correctly.
    date: _Date | None = None  # collected/result date


class LabsPayload(BaseModel):
    """Recent lab analytes for the bound patient, newest first."""

    model_config = ConfigDict(frozen=True)

    labs: tuple[LabResultItem, ...]


LabsResult = ToolResult[LabsPayload]


# JSON Schema offered to the LLM. since_days is optional; when the
# model omits it the fetcher passes its built-in default (90) to PHP,
# which clamps to a sane range server-side as defense in depth.
LABS_TOOL_SPEC = ToolSpec(
    name="get_recent_labs",
    description=(
        "Look up the active patient's recent lab results. Returns a "
        "flattened list of analytes (name, value, units, reference range, "
        "abnormal flag, collected date). Use when the user asks about "
        "labs, lab trends, or specific results like 'A1C' or 'creatinine'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": (
                    "How many days back to look. Defaults to 90 if omitted. "
                    "The server caps the upper bound at 365."
                ),
                "minimum": 1,
                "maximum": 365,
            },
        },
        "required": [],
    },
)


class LabsFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal labs endpoint.

    The PHP endpoint validates the bearer JWT and refuses requests whose
    patient_id claim doesn't match the requested pid, so callers must
    pass the *original* user-bound JWT (RequestContext.raw_token).
    """

    DEFAULT_SINCE_DAYS = 90

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/labs.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(
        self,
        patient_id: int,
        raw_token: str,
        since_days: int | None = None,
    ) -> LabsResult:
        url = f"{self._base_url}{self._path}"
        effective_since = since_days if since_days is not None else self.DEFAULT_SINCE_DAYS
        response = await self._client.get(
            url,
            params={"pid": patient_id, "since_days": effective_since},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = LabsPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_recent_labs",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.labs",
        )
        return LabsResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
