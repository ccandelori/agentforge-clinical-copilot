"""Schema + fetcher for the ``get_immunizations`` tool.

Mirrors the allergies/encounters tools: the LLM gets no input parameters,
because the patient context is bound at the request level (the JWT carries
patient_id; the orchestrator passes it in). The model only chooses
*whether* to call this tool, not who to call it about. See ARCHITECTURE.md
§4.

Backed by the ``immunizations`` table; vaccine_name is resolved from the
``codes`` table at code_type=100 (HL7 CVX). When a CVX has no codes-table
entry the agent still receives the raw cvx_code — the synthesis stage
can interpret common CVXs from the model's training data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class ImmunizationItem(BaseModel):
    """One immunization record from the patient's immunizations table."""

    model_config = ConfigDict(frozen=True)

    id: int
    cvx_code: str | None = None
    vaccine_name: str | None = None
    administered_date: date | None = None
    manufacturer: str | None = None
    lot_number: str | None = None
    note: str | None = None


class ImmunizationsPayload(BaseModel):
    """All immunization records (most recent first) for the bound patient."""

    model_config = ConfigDict(frozen=True)

    immunizations: tuple[ImmunizationItem, ...]


ImmunizationsResult = ToolResult[ImmunizationsPayload]


IMMUNIZATIONS_TOOL_SPEC = ToolSpec(
    name="get_immunizations",
    description=(
        "Look up the active patient's immunization history (vaccines "
        "administered, CVX codes, dates). Use when the user asks about "
        "vaccinations, immunization status, or what shots a patient has "
        "received."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


class ImmunizationsFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal immunizations endpoint.

    The PHP endpoint validates the bearer JWT and refuses requests whose
    patient_id claim doesn't match the requested pid, so callers must pass
    the *original* user-bound JWT (available on RequestContext.raw_token).
    """

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/immunizations.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(
        self, patient_id: int, raw_token: str
    ) -> ImmunizationsResult:
        url = f"{self._base_url}{self._path}"
        response = await self._client.get(
            url,
            params={"pid": patient_id},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = ImmunizationsPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_immunizations",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.immunizations",
        )
        return ImmunizationsResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
