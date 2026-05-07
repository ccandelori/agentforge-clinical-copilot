"""HTTP writer for browser-uploaded document bytes (T38.15).

Bridges the BFF ``POST /api/agent/upload`` route to the OpenEMR PHP
endpoint that lands raw multipart bytes in the documents table. The
PHP endpoint (``InternalUploadDocumentController``) validates the
bearer JWT and refuses requests whose claim's ``patient_id`` does not
match the resolved pid for the supplied ``patient_uuid`` — so callers
must forward the *user-bound* JWT (``RequestContext.raw_token``)
verbatim. The sidecar must never mint its own token for this call:
the patient-scope check lives on the JWT claim, not the URL.

Mirror image of :class:`DocumentBytesFetcher`: that class reads bytes
back out of the documents table, this one writes them in. The two
sides of the round-trip share auth posture, error shape, and base-URL
configuration.

See ARCHITECTURE.md §1 (sidecar ↔ OpenEMR boundary) and
``docs/NEXT-SESSION.md`` for the T38.15 wiring plan.
"""

from __future__ import annotations

from typing import Any

import httpx

# File-path URL matches the other internal endpoints. A reverse-proxy
# rewrite to ``/agentforge/internal`` is allowed in production but not
# assumed here so the sidecar works against a vanilla OpenEMR
# deployment without that rewrite configured.
_DEFAULT_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/upload_document.php"
)


class DocumentUploadError(RuntimeError):
    """Failure uploading document bytes to OpenEMR.

    ``status_code`` carries the upstream HTTP status when the request
    reached the server (401 / 403 / 404 / 5xx). Transport-level
    failures (DNS, TLS, connect refused) and contract violations
    (200 with a malformed body) raise with ``status_code`` set to
    ``0`` so the BFF route can distinguish "the sidecar couldn't
    talk to OpenEMR" (→ 503) from "OpenEMR returned an error"
    (→ 502 / 4xx pass-through).
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class DocumentUploadWriter:
    """JWT-authed writer for OpenEMR document uploads.

    Construction takes ``base_url`` because the sidecar runs as a
    separate process and may target a different host than its own
    reverse-proxy origin. ``http_client`` is injectable so tests can
    pass an :class:`httpx.MockTransport`-backed client without
    standing up the PHP endpoint.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        path: str = _DEFAULT_PATH,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        # 30s default — generous enough for a 10 MB PDF over a local
        # Docker bridge (the read-side cap from the bytes fetcher is
        # 10s; writes can be slower because the PHP side does the
        # encryption + filesystem store synchronously).
        self._client = http_client or httpx.AsyncClient(timeout=30.0)

    async def upload(
        self,
        *,
        jwt: str,
        patient_uuid: str,
        filename: str,
        content: bytes,
        mimetype: str,
        doc_type: str,
        encounter_id: int | None = None,
    ) -> int:
        """Upload one document and return its new id.

        ``jwt`` MUST be the user-bound JWT carried by the BFF route's
        ``RequestContext`` (the one minted from the cookie session +
        resolved /me + resolved /patient_pid). Forwarding it preserves
        the JWT-vs-pid scope check on the PHP side. Raises
        :class:`DocumentUploadError` on any non-2xx response, transport
        failure, or contract violation.
        """
        if not patient_uuid or not patient_uuid.strip():
            raise ValueError("patient_uuid must be a non-empty string")
        if not content:
            raise ValueError("content must be non-empty bytes")

        # ``files`` keyed by ``"file"`` matches the PHP side's
        # ``$request->files->get('file')`` lookup. Passing the
        # filename + content + mimetype as a 3-tuple is httpx's
        # contract for multipart file fields.
        files = {"file": (filename, content, mimetype)}
        data: dict[str, str] = {
            "patient_uuid": patient_uuid,
            "doc_type": doc_type,
        }
        if encounter_id is not None:
            data["encounter_id"] = str(encounter_id)

        url = f"{self._base_url}{self._path}"
        try:
            response = await self._client.post(
                url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {jwt}"},
            )
        except httpx.HTTPError as exc:
            raise DocumentUploadError(
                status_code=0,
                message="document-upload transport failure",
            ) from exc

        if response.status_code != 200:
            raise DocumentUploadError(
                status_code=response.status_code,
                message=(
                    f"document-upload upstream returned "
                    f"{response.status_code}"
                ),
            )

        # Parse the success body. The PHP side returns
        # ``{"document_id": int}``; anything else is a contract
        # violation we shouldn't paper over.
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise DocumentUploadError(
                status_code=0,
                message="document-upload response was not JSON",
            ) from exc

        if not isinstance(payload, dict):
            raise DocumentUploadError(
                status_code=0,
                message="document-upload response was not a JSON object",
            )

        document_id = payload.get("document_id")
        if not isinstance(document_id, int) or document_id <= 0:
            raise DocumentUploadError(
                status_code=0,
                message="document-upload response missing valid document_id",
            )

        return document_id

    async def aclose(self) -> None:
        await self._client.aclose()
