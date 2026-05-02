"""Schema + fetcher for the ``get_procedures`` tool.

Returns the patient's recent completed procedures: PHQ-9 / depression
screenings, AUDIT, IPV screens, surgical referrals, dental cleanings,
imaging, and other interventions that are recorded as procedure_order
rows but don't produce numeric lab values.

The PHP repository discriminates procedures from labs by checking
whether the order produced procedure_result rows (labs do; procedures
don't). It also dedups by procedure_code so an annually-recurring
screening collapses to one row (most recent).

Like the labs tool, this one accepts an optional ``since_days``
parameter so the agent can narrow the lookback. Default is 365 days
(procedures are typically annual or rarer; 90d would miss most
recurring screenings).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class ProcedureItem(BaseModel):
    """One procedure row from the patient's procedure_order table."""

    model_config = ConfigDict(frozen=True)

    id: int
    procedure_code: str | None = None
    procedure_name: str | None = None
    date_ordered: date | None = None
    status: str | None = None
    encounter_id: int | None = None


class ProceduresPayload(BaseModel):
    """Recent completed procedures for the bound patient (most recent first)."""

    model_config = ConfigDict(frozen=True)

    procedures: tuple[ProcedureItem, ...]


ProceduresResult = ToolResult[ProceduresPayload]


PROCEDURES_TOOL_SPEC = ToolSpec(
    name="get_procedures",
    description=(
        "Look up the active patient's recent completed procedures: "
        "screenings (depression, AUDIT, IPV), surgical interventions, "
        "imaging, dental work, and other interventions. Distinct from "
        "get_recent_labs (which covers analytes with values and ranges). "
        "Use when the user asks about screenings, surgical history, what "
        "procedures the patient has had, or whether a specific screen has "
        "been done."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": (
                    "How many days back to look. Defaults to 365 if "
                    "omitted. The server caps the upper bound at 1825 "
                    "(5 years)."
                ),
                "minimum": 1,
                "maximum": 1825,
            },
        },
        "required": [],
    },
)


class ProceduresFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal procedures endpoint.

    The PHP endpoint validates the bearer JWT and refuses requests whose
    patient_id claim doesn't match the requested pid, so callers must
    pass the *original* user-bound JWT (RequestContext.raw_token).
    """

    DEFAULT_SINCE_DAYS = 365

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/procedures.php"
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
    ) -> ProceduresResult:
        url = f"{self._base_url}{self._path}"
        effective_since = (
            since_days if since_days is not None else self.DEFAULT_SINCE_DAYS
        )
        response = await self._client.get(
            url,
            params={"pid": patient_id, "since_days": effective_since},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = ProceduresPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_procedures",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.procedures",
        )
        return ProceduresResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
