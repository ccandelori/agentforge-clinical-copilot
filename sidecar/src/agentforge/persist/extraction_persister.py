"""HTTP client for the OpenEMR persist endpoints (P1.1).

Mirrors :class:`agentforge.tools.document_bytes.DocumentBytesFetcher`
in shape: long-lived :class:`httpx.AsyncClient`, JWT bearer auth,
narrow typed exception class, generous timeout. The two methods
(:meth:`ExtractionPersister.persist_intake` /
:meth:`ExtractionPersister.persist_lab`) target the two W2 persist
controllers documented in ``ARCHITECTURE.md`` and the per-controller
docblocks under ``interface/modules/.../Controllers/Internal*Persist*``.

Body shape contract
-------------------

The PHP controllers consume ``document_id`` + ``patient_id`` + whatever
extraction-specific fields the Pydantic model emits. We rely on
``BaseModel.model_dump(mode="json")`` to walk ``date`` / ``StrEnum`` /
nested submodels into JSON-safe primitives — the same call site
:meth:`Orchestrator._run_graph_turn` already uses for the per-turn
extraction snapshot, so a controller-vs-snapshot drift can't sneak
in here without breaking the snapshot contract too.

Failure modes
-------------

The persister NEVER raises into the synthesis turn — that's the
orchestrator hook's contract, not ours. We surface failures as a
typed :class:`ExtractionPersistError` so the orchestrator's best-
effort log records the actual cause:

- 4xx → controller rejected the payload (programmer bug, bad scope,
  malformed body)
- 5xx → OpenEMR is broken
- transport (DNS, TLS, refused) → status_code 0, mirroring
  :class:`DocumentBytesFetchError`'s sentinel
- 2xx with a malformed response body (missing the resource id we
  contracted for) → status_code is the actual HTTP status; the caller
  can distinguish from upstream errors by the success-class status

Why not have the persister log itself? The orchestrator hook owns the
"this is best-effort" framing — letting it log keeps the message a
single source of truth and avoids double-logging the same failure once
in the persister and once in the hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx

from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.schemas.lab import LabPdfExtraction

# Same file-path URL convention as ``DocumentBytesFetcher`` — works
# against both vanilla OpenEMR and the production reverse-proxy
# rewrite to ``/agentforge/internal``. Tests pin the file paths.
_INTAKE_PATH: Final[str] = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/persist_questionnaire_response.php"
)
_LAB_PATH: Final[str] = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/persist_lab_result.php"
)


@dataclass(frozen=True, slots=True)
class PersistedHandle:
    """Identifier for a persisted resource, returned to the caller.

    ``kind`` distinguishes which controller produced the id so the
    dashboard's confirm-panel can route a future "open this resource"
    action to the right surface. ``resource_id`` is stringified at this
    boundary because the dashboard JSON layer already treats resource
    ids as strings (matches ``AgentTurnRequest.document_id`` shape) and
    avoids precision loss on very large integer ids.
    """

    resource_id: str
    kind: str


class ExtractionPersistError(RuntimeError):
    """Failure persisting an extraction to OpenEMR.

    ``status_code`` semantics mirror
    :class:`agentforge.tools.document_bytes.DocumentBytesFetchError`:
    upstream HTTP status when the request reached OpenEMR (4xx / 5xx),
    ``0`` for transport failures (DNS / TLS / refused). A 2xx-class
    status here means "the controller responded successfully but the
    body did not carry the resource id we expected" — a contract
    drift, not a transport or upstream error.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ExtractionPersister:
    """JWT-authed POST client for the OpenEMR persist controllers.

    Holds a long-lived :class:`httpx.AsyncClient` (10s timeout) so
    repeated turns within an asyncio task amortize the connection
    pool. ``http_client`` is injectable so tests can pass an
    :class:`httpx.MockTransport`-backed client without standing up the
    PHP endpoints.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # 10s default — generous for the controller's DBAL transaction
        # under load. The orchestrator hook caps the total turn budget
        # via :class:`asyncio.timeout`, so a hung persist call cannot
        # strand the per-turn timeout envelope on its own.
        self._client = http_client or httpx.AsyncClient(timeout=10.0)

    async def persist_intake(
        self,
        extraction: IntakeFormExtraction,
        *,
        patient_id: int,
        document_id: int,
        internal_jwt: str,
    ) -> PersistedHandle:
        """POST an :class:`IntakeFormExtraction` to the intake controller.

        ``patient_id`` and ``document_id`` are accepted explicitly even
        though the Pydantic shape carries them — passing them in the
        signature keeps the call site self-documenting and lets the
        orchestrator hook re-use the same values it threaded through
        the graph state without re-reading them off the model.
        """
        body = self._build_body(
            extraction, patient_id=patient_id, document_id=document_id
        )
        response = await self._post(
            path=_INTAKE_PATH,
            body=body,
            internal_jwt=internal_jwt,
        )
        resource_id = self._read_id(response, key="questionnaire_response_id")
        return PersistedHandle(
            resource_id=resource_id,
            kind="questionnaire_response",
        )

    async def persist_lab(
        self,
        extraction: LabPdfExtraction,
        *,
        patient_id: int,
        document_id: int,
        internal_jwt: str,
    ) -> PersistedHandle:
        """POST a :class:`LabPdfExtraction` to the lab controller."""
        body = self._build_body(
            extraction, patient_id=patient_id, document_id=document_id
        )
        response = await self._post(
            path=_LAB_PATH,
            body=body,
            internal_jwt=internal_jwt,
        )
        resource_id = self._read_id(response, key="procedure_order_id")
        return PersistedHandle(
            resource_id=resource_id,
            kind="procedure_order",
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_body(
        self,
        extraction: IntakeFormExtraction | LabPdfExtraction,
        *,
        patient_id: int,
        document_id: int,
    ) -> dict[str, object]:
        """Pydantic → JSON-safe dict, with the IDs forced from the call site.

        The Pydantic shape already carries ``document_id`` /
        ``patient_id``, but we overwrite them with the values the call
        site supplies to defend against an upstream graph state where
        the extraction was emitted with the wrong ids. The controllers'
        triple-check is the canonical authority on scope agreement;
        this just keeps the wire body internally consistent with the
        call args.
        """
        body = extraction.model_dump(mode="json")
        body["patient_id"] = patient_id
        body["document_id"] = document_id
        return body

    async def _post(
        self,
        *,
        path: str,
        body: dict[str, object],
        internal_jwt: str,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {internal_jwt}"},
            )
        except httpx.HTTPError as exc:
            raise ExtractionPersistError(
                status_code=0,
                message="extraction-persist transport failure",
            ) from exc
        if response.status_code >= 400:
            raise ExtractionPersistError(
                status_code=response.status_code,
                message=(
                    f"extraction-persist upstream returned "
                    f"{response.status_code}"
                ),
            )
        return response

    def _read_id(self, response: httpx.Response, *, key: str) -> str:
        """Parse the resource id off a successful response.

        Raises :class:`ExtractionPersistError` when the body doesn't
        carry the expected key — that's a controller-contract drift,
        not an upstream failure, but surfacing it as a typed error
        keeps the orchestrator's best-effort log uniform across all
        failure modes.
        """
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExtractionPersistError(
                status_code=response.status_code,
                message="extraction-persist response was not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ExtractionPersistError(
                status_code=response.status_code,
                message="extraction-persist response was not a JSON object",
            )
        raw = payload.get(key)
        if raw is None:
            raise ExtractionPersistError(
                status_code=response.status_code,
                message=(
                    f"extraction-persist response missing {key!r}"
                ),
            )
        return str(raw)
