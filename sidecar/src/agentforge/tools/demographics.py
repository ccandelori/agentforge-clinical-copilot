"""Schema + fetcher for the ``get_demographics`` tool.

The tool itself takes no input from the LLM — patient context is bound at
the request level (the JWT carries patient_id; the orchestrator passes it
in). The LLM only decides *whether* to call the tool, not who to call it
about. This keeps PHI access decisions inside the trust boundary defined
by the auth gateway. See ARCHITECTURE.md §4.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class DemographicsPayload(BaseModel):
    """Patient demographics the LLM uses to ground a clinical summary."""

    model_config = ConfigDict(frozen=True)

    patient_id: int
    given_name: str
    family_name: str
    date_of_birth: date  # ISO date
    sex: str | None = None  # "M" / "F" / "Other" / None
    preferred_language: str | None = None  # IETF BCP 47 tag, e.g. "en-US"


DemographicsResult = ToolResult[DemographicsPayload]


# JSON Schema offered to the LLM. No params: the orchestrator binds
# patient_id from the authenticated request context, so the model can't
# fetch demographics for a patient outside its session scope.
DEMOGRAPHICS_TOOL_SPEC = ToolSpec(
    name="get_demographics",
    description=(
        "Look up the active patient's demographics (legal name, date of birth, "
        "sex, preferred language). Use when summarizing the patient or when "
        "demographic context (age, language) is needed to answer the user."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)


class DemographicsFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal demographics endpoint.

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
            "/public/internal/demographics.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(self, patient_id: int, raw_token: str) -> DemographicsResult:
        url = f"{self._base_url}{self._path}"
        response = await self._client.get(
            url,
            params={"pid": patient_id},
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = DemographicsPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_demographics",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.demographics",
        )
        return DemographicsResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
