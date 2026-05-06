"""HTTP fetcher for raw document bytes (W2 MR 7).

Bridges the W2 LangGraph's intake-extractor node to the OpenEMR PHP
endpoint that streams a document's stored bytes back to the sidecar.
The PHP endpoint (``InternalDocumentBytesController``) validates the
bearer JWT and refuses requests whose claim's ``patient_id`` does not
match the document's owning patient — so callers must forward the
*user-bound* JWT (``RequestContext.raw_token``) verbatim. The sidecar
must never mint its own token for this call: the patient-scope check
lives on the JWT claim, not on the URL.

The fetcher is deliberately not a :class:`ToolResult`-shaped fetcher
(unlike :class:`LabsFetcher` etc.) because the LLM never invokes it
directly. The orchestrator calls it from the ``/turn`` route handler
when ``document_id`` is supplied on the request, renders the bytes
to per-page PNGs via :class:`PdfRenderer`, then hands the pages to
the graph as ``pdf_pages``.

See ARCHITECTURE.md §1 (sidecar ↔ OpenEMR boundary) and
``docs/NEXT-SESSION.md`` for the MR 7 wiring plan.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# File-path URL matches the other internal fetchers (LabsFetcher,
# NotesFetcher, etc.). A reverse-proxy rewrite to ``/agentforge/internal``
# is allowed in production but not assumed here so the sidecar works
# against a vanilla OpenEMR deployment without that rewrite configured.
_DEFAULT_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/get_document_bytes.php"
)


@dataclass(frozen=True, slots=True)
class DocumentBytes:
    """Raw bytes of an OpenEMR document with its MIME type.

    Bytes are held in memory; the consumer (PdfRenderer) opens the
    bytes via PyMuPDF without spilling to disk. The mimetype is
    parsed from the upstream ``Content-Type`` header with any
    ``charset=`` / boundary suffix stripped — callers compare it
    against ``application/pdf`` to decide whether the document is
    renderable, and a wrong mimetype is the canonical signal that
    a non-PDF was uploaded with a ``.pdf`` filename.
    """

    content: bytes
    mimetype: str


class DocumentBytesFetchError(RuntimeError):
    """Failure fetching document bytes from OpenEMR.

    ``status_code`` carries the upstream HTTP status when the request
    reached the server (401 / 403 / 404 / 5xx). Transport-level
    failures (DNS, TLS, connect refused) raise with ``status_code``
    set to ``0`` so the ``/turn`` route can distinguish "the sidecar
    couldn't reach OpenEMR" (→ 503) from "OpenEMR returned an error"
    (→ 502 / 4xx pass-through).
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class DocumentBytesFetcher:
    """JWT-authed fetcher for OpenEMR document bytes.

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
        # 10s default — generous for large PDFs (10-25 MB) over a
        # local Docker bridge while still bounded so a hung upstream
        # doesn't strand the per-turn timeout envelope.
        self._client = http_client or httpx.AsyncClient(timeout=10.0)

    async def fetch(self, *, document_id: int, raw_token: str) -> DocumentBytes:
        """Fetch one document's raw bytes by id.

        ``raw_token`` MUST be the user-bound JWT carried by the
        original ``/turn`` request (``RequestContext.raw_token``);
        forwarding it preserves the patient-scope check on the PHP
        side. Raises :class:`DocumentBytesFetchError` on any non-2xx
        response or transport failure.
        """
        if document_id <= 0:
            raise ValueError(f"document_id must be positive; got {document_id}")

        url = f"{self._base_url}{self._path}"
        try:
            response = await self._client.get(
                url,
                params={"document_id": document_id},
                headers={"Authorization": f"Bearer {raw_token}"},
            )
        except httpx.HTTPError as exc:
            raise DocumentBytesFetchError(
                status_code=0,
                message="document-bytes transport failure",
            ) from exc

        if response.status_code != 200:
            raise DocumentBytesFetchError(
                status_code=response.status_code,
                message=(
                    f"document-bytes upstream returned "
                    f"{response.status_code}"
                ),
            )

        # Strip ``charset=...`` / boundary parameters; we only care
        # about the canonical type. ``application/octet-stream`` is
        # the conservative fallback when the upstream omits the
        # header entirely (PHP's ``$response = new Response(...)``
        # always sets it, but bridges may strip).
        content_type = response.headers.get("Content-Type", "")
        mimetype = content_type.split(";", 1)[0].strip() or "application/octet-stream"

        return DocumentBytes(content=response.content, mimetype=mimetype)

    async def aclose(self) -> None:
        await self._client.aclose()
