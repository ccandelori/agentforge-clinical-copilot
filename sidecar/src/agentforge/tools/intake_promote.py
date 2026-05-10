"""HTTP writer for the OpenEMR intake-promotion endpoint (Gap 2).

Bridges the BFF ``POST /api/agent/promote/intake`` route to the
JWT-authed PHP endpoint that lands clinician-approved rows in the
``lists`` table. Mirrors the shape of
:class:`agentforge.tools.document_upload.DocumentUploadWriter`:

* long-lived :class:`httpx.AsyncClient` so successive promotions
  amortize the connection pool;
* narrow typed exception (:class:`IntakePromoteError`) carrying an
  ``status_code`` field with the same conventions as the upload
  writer (0 → transport failure, 4xx/5xx → upstream HTTP);
* path injectable so tests can pin the URL without standing up the
  full PHP endpoint;
* base URL injectable so we can target the dev-easy OpenEMR base
  URL or the production reverse-proxy origin.

The sidecar must forward the *user-bound* JWT it just minted from
the session — the PHP side's scope check is on the JWT's
``patientId`` claim, so reusing the same minter the upload + turn
routes use keeps the audit trail coherent.
"""

from __future__ import annotations

from typing import Any

import httpx

# File-path URL matches the other internal endpoints (allergies.php,
# patient_pid.php, persist_questionnaire_response.php, etc.). Production
# may add a reverse-proxy rewrite to ``/agentforge/internal``; the
# sidecar still works against vanilla OpenEMR without that rewrite.
_DEFAULT_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/promote_intake.php"
)


class IntakePromoteError(RuntimeError):
    """Failure committing accepted intake items to OpenEMR.

    ``status_code`` carries the upstream HTTP status when the request
    reached the server (4xx / 5xx). Transport-level failures (DNS,
    TLS, connect refused) and contract violations (200 with a
    malformed body) raise with ``status_code`` set to ``0`` so the
    BFF route can distinguish "the sidecar couldn't talk to OpenEMR"
    (→ 503) from "OpenEMR returned an error" (→ 502 / 4xx
    pass-through).
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class IntakePromoteWriter:
    """JWT-authed POST client for the OpenEMR intake-promote endpoint.

    ``base_url`` is the OpenEMR origin (e.g. dev-easy's
    ``http://localhost:8300`` or the production reverse-proxy). The
    PHP path is appended to that. ``http_client`` is injectable so
    tests can use :class:`httpx.MockTransport` without spinning up the
    PHP endpoint.
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
        # 15s default — middle ground between the document-upload
        # writer's 30s (multipart) and the document-bytes fetcher's
        # 10s. A typical promote batch writes <10 lists rows, so the
        # PHP transactional() call is fast.
        self._client = http_client or httpx.AsyncClient(timeout=15.0)

    async def promote(
        self,
        *,
        jwt: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST the accepted-items body and return the parsed receipt.

        Raises :class:`IntakePromoteError` on any failure (transport
        or upstream); on success returns the parsed JSON body the
        controller emitted (``{promoted: [...], count: N}``).
        """
        url = f"{self._base_url}{self._path}"
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {jwt}"},
            )
        except httpx.HTTPError as exc:
            raise IntakePromoteError(
                status_code=0,
                message="intake-promote transport failure",
            ) from exc

        if response.status_code >= 400:
            raise IntakePromoteError(
                status_code=response.status_code,
                message=(
                    f"intake-promote upstream returned "
                    f"{response.status_code}"
                ),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise IntakePromoteError(
                status_code=response.status_code,
                message="intake-promote response was not valid JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise IntakePromoteError(
                status_code=response.status_code,
                message="intake-promote response was not a JSON object",
            )
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()
