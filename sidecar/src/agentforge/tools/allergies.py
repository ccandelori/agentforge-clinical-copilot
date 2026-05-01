"""Schema + fetcher for the ``get_active_allergies`` tool.

Mirrors the medications/problems tools: the LLM gets no input parameters,
because the patient context is bound at the request level (the JWT carries
patient_id; the orchestrator passes it in). The model only chooses
*whether* to call this tool, not who to call it about. See ARCHITECTURE.md
§4.

Backed by the ``lists`` table with type='allergy' — same direct-DB shape
the medications and problems tools use. The original task spec called for
a FHIR AllergyIntolerance read; we deviated to keep the four MVP tools on
one access pattern. Logged in docs/DEVIATIONS.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class AllergyItem(BaseModel):
    """One active allergy row from the patient's lists table."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    reaction: str | None = None
    severity: str | None = None
    begin_date: date | None = None
    end_date: date | None = None


class AllergiesPayload(BaseModel):
    """All currently active allergies for the bound patient."""

    model_config = ConfigDict(frozen=True)

    allergies: tuple[AllergyItem, ...]


AllergiesResult = ToolResult[AllergiesPayload]


ALLERGIES_TOOL_SPEC = ToolSpec(
    name="get_active_allergies",
    description=(
        "Look up the active patient's known allergies (allergens, reactions, "
        "severity). Use when the user asks about allergies, intolerances, or "
        "before recommending a medication."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


class AllergiesFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal allergies endpoint.

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
            "/public/internal/allergies.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(self, patient_id: int, raw_token: str) -> AllergiesResult:
        url = f"{self._base_url}{self._path}"
        response = await self._client.get(
            url,
            params={"pid": patient_id},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = AllergiesPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_active_allergies",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.allergies",
        )
        return AllergiesResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
