"""``POST /api/agent/upload`` — the BFF document-upload route (T38.15).

The dashboard-facing upload endpoint that lands a multipart document
in the OpenEMR document store via the JWT-authed
``InternalUploadDocumentController`` PHP endpoint. Same auth pipeline
as :mod:`agentforge.dashboard_auth.turn_route`:

  1. **Cookie → Session.** Resolve the session record from the
     HttpOnly cookie; reject early if the session is missing or has
     no ``fhir_user`` claim.
  2. **Session → OpenEMR identity** (cached per-access-token).
  3. **Identity + body → Internal JWT.** :class:`InternalJwtMinter`
     produces the same JWT shape the legacy PHP module mints.
  4. **Internal JWT → RequestContext.** :class:`AuthGateway`
     validates the minted JWT exactly as it validates a /turn request.
  5. **DocumentUploadWriter.** Forwards the user-bound JWT plus
     multipart bytes to the OpenEMR PHP endpoint, returns the new
     ``document_id`` to the browser.

The split between ``/turn`` and ``/upload`` keeps each route's HTTP
contract focused (JSON in, JSON out for /turn; multipart in, JSON out
for /upload) while sharing the auth pipeline that ADR-0001 specifies
as the single trust chokepoint.

The browser never sees the internal JWT — the BFF mints it from the
session cookie's identity claims. A leaked JWT would be a separate
trust problem, not a contract issue.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Protocol

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

_log = logging.getLogger(__name__)

from agentforge.config import Settings
from agentforge.dashboard_auth.internal_jwt import (
    InternalJwtMintError,
    InternalJwtMinter,
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
from agentforge.tools.document_upload import DocumentUploadError


# Allowlist for the ``Content-Type`` of the uploaded file. Lab PDFs
# are the dominant case; intake-form scans may arrive as JPEG or PNG
# (mobile camera captures). Anything else is a 415 — vue-ui's upload
# control should restrict the picker accordingly, but the BFF is the
# load-bearing check.
_ALLOWED_MIMETYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
)

# Closed set of accepted document categories — must match the PHP
# side's ``InternalUploadDocumentController::ALLOWED_DOC_TYPES``.
_ALLOWED_DOC_TYPES: frozenset[str] = frozenset({"lab_pdf", "intake_form"})

# 10 MB BFF cap. The PHP side has a higher (25 MB) ceiling, so this
# is the effective limit; we surface it as 413 with a clear message
# rather than a generic 400.
_MAX_BYTES = 10 * 1024 * 1024


class _UploadWriterProto(Protocol):
    """Subset of :class:`DocumentUploadWriter` the route depends on.

    Typed as a Protocol so the test suite can inject a stub without
    constructing an httpx client.
    """

    async def upload(  # pragma: no cover — protocol stub
        self,
        *,
        jwt: str,
        patient_uuid: str,
        filename: str,
        content: bytes,
        mimetype: str,
        doc_type: str,
        encounter_id: int | None = None,
    ) -> int: ...


def _extract_user_uuid(fhir_user: str) -> str | None:
    """Pull the trailing UUID off a ``fhirUser`` claim.

    Mirrors the helper in :mod:`turn_route` — accepts either the full
    URI form (``…/fhir/Practitioner/<uuid>``) OpenEMR emits in
    production, or the bare ``Practitioner/<uuid>`` form some
    environments use.
    """
    if not fhir_user:
        return None
    tail = fhir_user.rsplit("/", 1)[-1].strip()
    return tail or None


def make_agent_upload_router(
    *,
    settings: Settings,
    session_store: SessionStore,
    me_fetcher: OpenEMRMeFetcher,
    patient_pid_fetcher: OpenEMRPatientPidFetcher,
    jwt_minter: InternalJwtMinter,
    auth_gateway: AuthGateway,
    document_upload_writer: _UploadWriterProto,
) -> APIRouter:
    """Build the BFF agent-upload router. Mounted at ``/api/agent``."""
    router = APIRouter(prefix="/api/agent", tags=["dashboard-agent"])

    # Identity / pid caches mirror the turn-route's so a tight
    # upload-then-turn flow doesn't re-hit /me + /patient_pid for a
    # session that just made the same lookups. Note: this is a
    # process-local cache; the routers don't share their dicts. That's
    # intentional — keeps each route's surface independently testable
    # and the cache hit ratio degrades from 100% to ~50% on first load
    # of a freshly mounted router, which is a tolerable tax for the
    # boundary clarity.
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
                "Upload bridge /me failed: upstream_status=%s user_uuid=%s err=%s",
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
                "Upload bridge /patient_pid failed: upstream_status=%s "
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

    @router.post("/upload")
    async def agent_upload(
        request: Request,
        file: Annotated[UploadFile, File(...)],
        patient_uuid: Annotated[str, Form(min_length=1)],
        doc_type: Annotated[str, Form(min_length=1)],
        encounter_id: Annotated[int | None, Form()] = None,
    ) -> dict[str, Any]:
        # ---- Allowlist + size enforcement (415 / 413 / 422) ----
        if doc_type not in _ALLOWED_DOC_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"doc_type must be one of: "
                    f"{sorted(_ALLOWED_DOC_TYPES)}"
                ),
            )

        mimetype = file.content_type or "application/octet-stream"
        if mimetype not in _ALLOWED_MIMETYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    f"Content-Type {mimetype!r} is not allowed. "
                    f"Allowed: {sorted(_ALLOWED_MIMETYPES)}"
                ),
            )

        # Read the body into memory. UploadFile holds the bytes in a
        # SpooledTemporaryFile, so a 10 MB cap fits comfortably without
        # spilling. We check size after read because UploadFile.size
        # is unreliable cross-client.
        content = await file.read()
        if len(content) > _MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"Uploaded file exceeds {_MAX_BYTES}-byte BFF limit."
                ),
            )
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        # ---- Auth pipeline (401 / 502 paths from the resolvers) ----
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

        # AuthGateway validation is the trust chokepoint per ADR-0001:
        # we go through it even though we know the JWT is well-formed
        # (we just minted it) so a future change to the gateway's
        # validation rules picks up both routes uniformly.
        await auth_gateway.validate_request(f"Bearer {internal_jwt}")

        # ---- Forward to OpenEMR via the JWT-authed PHP endpoint ----
        filename = file.filename or "upload.bin"
        try:
            document_id = await document_upload_writer.upload(
                jwt=internal_jwt,
                patient_uuid=patient_uuid,
                filename=filename,
                content=content,
                mimetype=mimetype,
                doc_type=doc_type,
                encounter_id=encounter_id,
            )
        except DocumentUploadError as exc:
            if exc.status_code == 0:
                # Transport failure (sidecar can't reach OpenEMR) → 503.
                # Same shape the /turn route returns for upstream
                # transport failures so the panel can react uniformly.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Document upload failed: OpenEMR unreachable.",
                ) from exc
            # Upstream returned an error (401 / 403 / 404 / 5xx). The
            # panel needs to know whether the failure was the BFF's
            # fault or OpenEMR's, so we surface the upstream status.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "Document upload failed.",
                    "sidecar_upstream_status": exc.status_code,
                },
            ) from exc

        return {"document_id": document_id}

    return router
