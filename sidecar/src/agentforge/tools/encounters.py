"""Schema + fetcher for the ``get_recent_encounters`` tool.

Encounters carry their own sensitivity signals on the row:

  * ``pc_catid`` (postcalendar category, int) — passed to the gateway
    as :class:`RecordMetadata.encounter_category`. Behavioral-health
    and psychiatric encounter categories live here.
  * ``sensitivity`` (string) — explicit sensitivity marker
    (``behavioral_health``, ``substance_abuse_cfr42``, etc.) that the
    fetcher passes as ``RecordMetadata.note_type`` so policy rules
    keyed on either field can match.

The gating contract mirrors :mod:`agentforge.tools.notes`: rows the
user lacks clearance for survive in the result with
``permission_denied=True`` and stripped content (``reason``,
``provider_name``), so the model can describe what was withheld in
aggregate without seeing the protected text.

Spec deviation: Task 21 spec calls for the OpenEMR FHIR
``Encounter`` endpoint. We mirror the AgentForge custom-internal-
endpoint pattern (``recent_encounters.php``) instead — see
docs/DEVIATIONS.md.

See ARCHITECTURE.md S2 (sensitivity) and S4 (tools).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from agentforge.gateway.auth_gateway import RecordMetadata, RequestContext
from agentforge.llm.types import ToolSpec
from agentforge.tools.dtos import ToolResult, ToolResultMetadata


class _VisibilityGate(Protocol):
    async def check_record_visibility(
        self, ctx: RequestContext, metadata: RecordMetadata
    ) -> bool: ...


class EncounterItem(BaseModel):
    """One encounter row from form_encounter, possibly redacted."""

    model_config = ConfigDict(frozen=True)

    id: int
    date: str | None = None
    reason: str | None = None
    encounter_type: str | None = None
    class_code: str | None = None
    provider_id: int | None = None
    provider_name: str | None = None
    sensitivity: str | None = None
    encounter_category: int | None = None
    permission_denied: bool = False


class EncountersPayload(BaseModel):
    """Recent encounters for the bound patient, newest first, gated."""

    model_config = ConfigDict(frozen=True)

    encounters: tuple[EncounterItem, ...]


EncountersResult = ToolResult[EncountersPayload]


ENCOUNTERS_TOOL_SPEC = ToolSpec(
    name="get_recent_encounters",
    description=(
        "Look up the active patient's recent encounters (visits, "
        "consults, follow-ups) from the chart's encounter history. "
        "Returns date, reason, encounter type, provider, and visit "
        "class for each encounter, newest first. Rows the calling "
        "user lacks clearance for come back with permission_denied=true "
        "and an empty reason — surface them as 'N restricted "
        "encounters' rather than ignoring silently."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "since_days": {
                "type": "integer",
                "description": (
                    "How many days back to look. Defaults to 365 if "
                    "omitted. The server caps the upper bound at 730."
                ),
                "minimum": 1,
                "maximum": 730,
            },
        },
        "required": [],
    },
)


class EncountersFetcher:
    """HTTP fetcher + per-record sensitivity gating for recent_encounters."""

    DEFAULT_SINCE_DAYS = 365

    def __init__(
        self,
        base_url: str,
        gateway: _VisibilityGate,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/recent_encounters.php"
        ),
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._gateway = gateway
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._cache_ttl = cache_ttl_seconds

    async def fetch(
        self,
        ctx: RequestContext,
        since_days: int | None = None,
    ) -> EncountersResult:
        url = f"{self._base_url}{self._path}"
        effective_since = (
            since_days if since_days is not None else self.DEFAULT_SINCE_DAYS
        )
        response = await self._client.get(
            url,
            params={"pid": ctx.patient_id, "since_days": effective_since},
            headers={"Authorization": f"Bearer {ctx.raw_token}"},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()

        gated_rows: list[EncounterItem] = []
        any_denied = False
        for enc in body.get("encounters", []):
            cat_raw = enc.get("encounter_category")
            sens_raw = enc.get("sensitivity")
            metadata = RecordMetadata(
                encounter_category=cat_raw if isinstance(cat_raw, int) else None,
                # Pass the explicit sensitivity marker as note_type so
                # behavioral_health / substance_abuse_cfr42 policy rules
                # match either via category or via the string flag.
                note_type=sens_raw if isinstance(sens_raw, str) else None,
                attending_only=False,
            )
            allowed = await self._gateway.check_record_visibility(ctx, metadata)
            provider_id_raw = enc.get("provider_id")
            provider_id = (
                provider_id_raw if isinstance(provider_id_raw, int) else None
            )
            if not allowed:
                any_denied = True
                gated_rows.append(
                    EncounterItem(
                        id=int(enc["id"]),
                        date=enc.get("date"),
                        # reason and provider_name leak content/identity;
                        # strip both. encounter_type / class_code /
                        # category / sensitivity stay as classifiers
                        # for the model's aggregate summary.
                        reason=None,
                        encounter_type=enc.get("encounter_type"),
                        class_code=enc.get("class_code"),
                        provider_id=provider_id,
                        provider_name=None,
                        sensitivity=enc.get("sensitivity"),
                        encounter_category=cat_raw if isinstance(cat_raw, int) else None,
                        permission_denied=True,
                    )
                )
                continue
            gated_rows.append(
                EncounterItem(
                    id=int(enc["id"]),
                    date=enc.get("date"),
                    reason=enc.get("reason"),
                    encounter_type=enc.get("encounter_type"),
                    class_code=enc.get("class_code"),
                    provider_id=provider_id,
                    provider_name=enc.get("provider_name"),
                    sensitivity=enc.get("sensitivity"),
                    encounter_category=cat_raw if isinstance(cat_raw, int) else None,
                    permission_denied=False,
                )
            )

        payload = EncountersPayload(encounters=tuple(gated_rows))
        result_metadata = ToolResultMetadata(
            tool_name="get_recent_encounters",
            fetched_at=datetime.now(UTC),
            data_freshness_seconds=self._cache_ttl,
            source="openemr.encounters",
            redaction_applied=any_denied,
            redacted_fields=(
                ("reason", "provider_name") if any_denied else ()
            ),
        )
        return EncountersResult(metadata=result_metadata, payload=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
