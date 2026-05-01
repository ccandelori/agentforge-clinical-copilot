"""Schema + fetcher for the ``get_vitals_trend`` tool.

Unlike demographics / problems / medications, this tool *does* take an
input parameter — the LLM picks ``since_days`` to scope the lookback
window (e.g. "show me trends over the last two weeks"). Patient context
is still bound at the request level: the JWT carries patient_id; the
orchestrator passes it in. The model only chooses *whether*, *how far
back*, but never *who about*. See ARCHITECTURE.md §4.

The PHP repository is responsible for dropping clinically-impossible
zero values to ``null`` before they reach the model — see
``VitalsRepository`` for the gotchas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class VitalsItem(BaseModel):
    """One vitals row scoped to the bound patient.

    Every numeric vital is nullable: the OpenEMR schema defaults missing
    values to ``0.00``, which the repository surfaces as ``None`` because
    a zero weight or systolic pressure is clinically meaningless.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    date: str | None = None
    systolic: int | None = None
    diastolic: int | None = None
    pulse: float | None = None
    respiration: float | None = None
    temperature: float | None = None
    temp_method: str | None = None
    oxygen_saturation: float | None = None
    height: float | None = None
    weight: float | None = None
    bmi: float | None = None
    bmi_status: str | None = None
    note: str | None = None


class VitalsPayload(BaseModel):
    """Recent vitals for the bound patient, newest first."""

    model_config = ConfigDict(frozen=True)

    vitals: tuple[VitalsItem, ...]


VitalsResult = ToolResult[VitalsPayload]


# JSON Schema offered to the LLM. The single ``since_days`` parameter
# is the only knob the model gets — patient_id is bound from the
# authenticated request context server-side.
VITALS_TOOL_SPEC = ToolSpec(
    name="get_vitals_trend",
    description=(
        "Look up the active patient's recent vital sign measurements "
        "(BP, pulse, temp, respiration, SpO2, height, weight, BMI). "
        "Use when the user asks about vitals, blood pressure trends, "
        "weight changes, or pre-visit summary."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": (
                    "Number of days of history to fetch. Default 90; "
                    "values are clamped to [1, 730] server-side."
                ),
                "default": 90,
                "minimum": 1,
                "maximum": 730,
            },
        },
        "required": [],
    },
)


class VitalsFetcher:
    """HTTP fetcher that calls the OpenEMR PHP internal vitals endpoint.

    The PHP endpoint validates the bearer JWT and refuses requests whose
    patient_id claim doesn't match the requested pid, so callers must pass
    the *original* user-bound JWT (available on RequestContext.raw_token).
    """

    DEFAULT_SINCE_DAYS = 90

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/vitals_trend.php"
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
    ) -> VitalsResult:
        url = f"{self._base_url}{self._path}"
        params: dict[str, int] = {"pid": patient_id}
        # Omit the ``since`` query param entirely when the model didn't
        # supply one — the PHP controller has its own default and we
        # don't want two layers fighting over which "default" wins.
        if since_days is not None:
            params["since"] = since_days

        response = await self._client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        payload = VitalsPayload.model_validate(body)
        metadata = ToolResultMetadata(
            tool_name="get_vitals_trend",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.form_vitals",
        )
        return VitalsResult(metadata=metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
