"""``GET /api/agent/document/{document_id}`` — the BFF document-fetch route.

The dashboard-facing half of the citation overlay (T38.16): vue-ui's
``<DocumentViewer>`` calls this endpoint to load a stored PDF (or
scanned image) inline when a citation pill points at a document. The
returned bytes ride the same auth pipeline as the agent-turn route —
cookie session → resolved identity → minted internal JWT → the JWT-
authed PHP fetcher (:class:`DocumentBytesFetcher`).

Design notes
------------

* ``patient_uuid`` is a *required* query parameter, not a server-side
  lookup. The caller (vue-ui) is mounted inside a patient chart, so it
  always knows which patient owns the document. Asserting that intent
  on the request keeps the JWT scoped to the right patient and
  preserves the patient-scope check the PHP endpoint enforces — no
  extra round-trip to resolve "which patient owns this document".
* Bytes ride in memory (``Response(content=..., media_type=...)``).
  Stored OpenEMR documents are O(MB); we don't need streaming and
  ``StreamingResponse`` would force the fetcher to expose an iterator
  it doesn't currently have.
* Error mapping mirrors the legacy ``/turn`` document-fetch path:
  upstream 403/404 pass through, transport / 5xx → 502.

The auth-pipeline implementation duplicates ``turn_route.py`` on
purpose — a deadline slice tolerates more duplication than churn.
A future refactor can extract the resolver helpers.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from agentforge.config import Settings
from agentforge.dashboard_auth.internal_jwt import (
    InternalJwtMinter,
    InternalJwtMintError,
)
from agentforge.dashboard_auth.openemr_me import (
    OpenEMRIdentity,
    OpenEMRMeFetcher,
    OpenEMRMeFetchError,
)
from agentforge.dashboard_auth.openemr_patient_pid import (
    OpenEMRPatientPidFetcher,
    OpenEMRPatientPidFetchError,
)
from agentforge.dashboard_auth.sessions import Session, SessionStore
from agentforge.gateway.auth_gateway import AuthGateway
from agentforge.tools.document_bytes import (
    DocumentBytes,
    DocumentBytesFetchError,
)

_log = logging.getLogger(__name__)


class _DocumentBytesFetcherProto:
    """Subset of :class:`DocumentBytesFetcher` the route depends on.

    Typed as a structural placeholder so tests can inject a fake
    fetcher without importing the real class's transport stack.
    """

    async def fetch(  # pragma: no cover — protocol stub
        self,
        *,
        document_id: int,
        raw_token: str,
    ) -> DocumentBytes: ...


def _extract_user_uuid(fhir_user: str) -> str | None:
    """Pull the trailing UUID off a ``fhirUser`` claim.

    Same shape acceptance as :func:`turn_route._extract_user_uuid` —
    accepts either the full URI form (``…/fhir/Practitioner/<uuid>``)
    or the bare ``Practitioner/<uuid>`` form.
    """
    if not fhir_user:
        return None
    tail = fhir_user.rsplit("/", 1)[-1].strip()
    return tail or None


def make_agent_document_router(
    *,
    settings: Settings,
    session_store: SessionStore,
    me_fetcher: OpenEMRMeFetcher,
    patient_pid_fetcher: OpenEMRPatientPidFetcher,
    jwt_minter: InternalJwtMinter,
    auth_gateway: AuthGateway,
    document_bytes_fetcher: _DocumentBytesFetcherProto,
) -> APIRouter:
    """Build the BFF document-fetch router. Mounted at ``/api/agent``."""
    router = APIRouter(prefix="/api/agent", tags=["dashboard-agent"])

    # Same caches as turn_route — keyed identically so a turn followed
    # by a document fetch on the same session+patient avoids two
    # /me + /patient_pid round-trips.
    identity_cache: dict[str, OpenEMRIdentity] = {}
    patient_pid_cache: dict[str, int] = {}

    async def _resolve_session(request: Request) -> Session:
        cookie = request.cookies.get(settings.dashboard_session_cookie_name)
        if not cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        session = await session_store.get_session(cookie)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
            )
        return session

    async def _resolve_identity(session: Session) -> OpenEMRIdentity:
        user_uuid = _extract_user_uuid(session.fhir_user or "")
        if user_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has no usable fhirUser identity",
            )
        cached = identity_cache.get(session.access_token)
        if cached is not None:
            return cached
        try:
            identity = await me_fetcher.fetch(user_uuid=user_uuid)
        except OpenEMRMeFetchError as exc:
            _log.warning(
                "Bridge /me failed: upstream_status=%s user_uuid=%s err=%s",
                exc.status_code,
                user_uuid,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "OpenEMR identity bootstrap failed.",
                    "upstream_status": exc.status_code,
                    "stage": "me",
                },
            ) from exc
        identity_cache[session.access_token] = identity
        return identity

    async def _resolve_patient_pid(patient_uuid: str) -> int:
        cached = patient_pid_cache.get(patient_uuid)
        if cached is not None:
            return cached
        try:
            pid = await patient_pid_fetcher.fetch(patient_uuid=patient_uuid)
        except OpenEMRPatientPidFetchError as exc:
            _log.warning(
                "Bridge /patient_pid failed: upstream_status=%s "
                "patient_uuid=%s err=%s",
                exc.status_code,
                patient_uuid,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "OpenEMR patient bootstrap failed.",
                    "upstream_status": exc.status_code,
                    "stage": "patient_pid",
                },
            ) from exc
        patient_pid_cache[patient_uuid] = pid
        return pid

    @router.get("/document/{document_id}")
    async def agent_document(
        request: Request,
        document_id: int,
        patient_uuid: Annotated[str, Query(min_length=1)],
    ) -> Response:
        session = await _resolve_session(request)
        identity = await _resolve_identity(session)
        patient_id = await _resolve_patient_pid(patient_uuid)

        try:
            internal_jwt = jwt_minter.mint(
                identity=identity,
                patient_id=patient_id,
            )
        except InternalJwtMintError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Run the JWT through the gateway so the route sits on the
        # exact validation chokepoint the legacy /turn surface uses;
        # the gateway also returns the canonical ``raw_token`` the
        # downstream fetcher needs to forward.
        ctx = await auth_gateway.validate_request(f"Bearer {internal_jwt}")

        try:
            document = await document_bytes_fetcher.fetch(
                document_id=document_id,
                raw_token=ctx.raw_token,
            )
        except DocumentBytesFetchError as exc:
            # Pass through 403/404 so vue-ui can render the right
            # message; everything else (transport failure, 5xx, weird
            # 4xx) bundles into 502 (bad gateway) — the BFF itself is
            # fine, but the upstream fetch failed.
            if exc.status_code in (403, 404):
                _log.info(
                    "document fetch upstream %s for document_id=%s",
                    exc.status_code,
                    document_id,
                )
                raise HTTPException(status_code=exc.status_code) from exc
            _log.warning(
                "document fetch upstream failed: status=%s document_id=%s",
                exc.status_code,
                document_id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "Document fetch failed.",
                    "sidecar_upstream_status": exc.status_code,
                },
            ) from exc

        return Response(content=document.content, media_type=document.mimetype)

    return router
