"""Schema + fetcher for the ``get_active_medications`` tool.

Like ``get_demographics``, this tool takes no input from the LLM — patient
context is bound at the request level (the JWT carries patient_id; the
orchestrator passes it in). The LLM only decides *whether* to call the
tool, not who to call it about. See ARCHITECTURE.md §4.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class MedicationItem(BaseModel):
    """One active medication row from the patient's lists table."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    begin_date: date | None = None
    end_date: date | None = None


class MedicationsPayload(BaseModel):
    """All currently active medications for the bound patient."""

    model_config = ConfigDict(frozen=True)

    medications: tuple[MedicationItem, ...]


MedicationsResult = ToolResult[MedicationsPayload]


# JSON Schema offered to the LLM. No params: the orchestrator binds
# patient_id from the authenticated request context, so the model can't
# fetch medications for a patient outside its session scope.
MEDICATIONS_TOOL_SPEC = ToolSpec(
    name="get_active_medications",
    description=(
        "Look up the active patient's currently active medications. Returns a "
        "list of medication names with optional begin/end dates. Use when the "
        "user asks about meds, drug interactions, or medication reconciliation."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


class MedicationsFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal medications endpoint.

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
            "/public/internal/medications.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(self, patient_id: int, raw_token: str) -> MedicationsResult:
        url = f"{self._base_url}{self._path}"
        response = await self._client.get(
            url,
            params={"pid": patient_id},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = MedicationsPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_active_medications",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.medications",
        )
        return MedicationsResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
