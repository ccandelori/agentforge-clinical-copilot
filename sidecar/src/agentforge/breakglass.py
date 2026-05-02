"""Break-the-glass audit logging tool.

Fires when a clinician opens a chart under break-the-glass intent
(``RequestContext.breakglass_flag = True`` with a ``breakglass_reason``
text). Writes the reason to OpenEMR's ``log.comments`` via the PHP
internal endpoint, where the existing EventAuditLogger writes the row
into the ``log`` table (encrypted at rest if site config has audit
encryption on, base64-encoded otherwise — either way, the reason text
lives there and only there).

Two contracts the tool enforces:

  * **Idempotent per session.** The first call for a given
    ``(user_id, patient_id, session_id)`` triple writes the audit row;
    subsequent calls during that session no-op so a chart with N tool
    calls doesn't produce N audit rows. Dedup is in-memory and lives
    for the sidecar process lifetime — short-lived enough (75 min
    session TTL) that we don't need cross-process coordination.

  * **Never raises on transport failures.** A failed audit is bad,
    but a turn that errors because the audit endpoint is down is
    worse — the user-facing flow has to keep working. Failures get
    classified into :class:`AuditOutcome` so the caller can monitor
    or alert without exception handling.

The Langfuse trace already carries ``breakglass_flag`` (boolean only,
no reason text) via :func:`AgentLangfuse.trace_turn` — covered by
Task 32. This module is the PHI side of the audit story.

See ARCHITECTURE.md S2 (sensitivity / breakglass policy) and the
DEVIATIONS log entry on 2026-05-01 about breakglass *not* being a
silent visibility bypass — only the audit path consumes the intent.
"""

from __future__ import annotations

import logging
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)


class AuditOutcome(StrEnum):
    """Result of a single :meth:`BreakglassAuditTool.log_breakglass_access` call."""

    NO_BREAKGLASS = "no_breakglass"  # ctx.breakglass_flag is False or reason absent
    ALREADY_LOGGED = "already_logged"  # session dedup hit
    LOGGED = "logged"                # newly written audit row
    AUDIT_FAILED = "audit_failed"    # PHP error / network — caller proceeds


# Sentinel that stands in for "no session_id provided" when building
# the dedup key. Using a string sentinel rather than ``None`` keeps the
# key shape uniform (always a tuple of three strings).
_NO_SESSION_SENTINEL = "no-session"


class BreakglassAuditTool:
    """POSTs the break-the-glass reason to the OpenEMR audit endpoint."""

    def __init__(
        self,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        path: str = (
            "/interface/modules/custom_modules/oe-module-agentforge"
            "/public/internal/log_breakglass.php"
        ),
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._path = path
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        # Per-process dedup. Keys: (user_id, patient_id, session_id_or_sentinel).
        self._logged_sessions: set[tuple[int, int, str]] = set()

    async def log_breakglass_access(
        self,
        ctx: object,
        session_id: str | None = None,
    ) -> AuditOutcome:
        """Audit a breakglass access; idempotent within a session.

        ``ctx`` is duck-typed as :class:`RequestContext` — the tool reads
        ``user_id``, ``patient_id``, ``breakglass_flag``,
        ``breakglass_reason``, and ``raw_token``. Typed as ``object`` so
        the tool stays loosely coupled to the gateway module's import
        graph; mypy gates the call sites.
        """
        # Local rebinding so mypy can narrow on the duck-typed object.
        flag = bool(getattr(ctx, "breakglass_flag", False))
        reason = getattr(ctx, "breakglass_reason", None)
        user_id = int(getattr(ctx, "user_id", 0))
        patient_id = int(getattr(ctx, "patient_id", 0))
        raw_token = str(getattr(ctx, "raw_token", ""))

        if not flag:
            return AuditOutcome.NO_BREAKGLASS
        if not isinstance(reason, str):
            return AuditOutcome.NO_BREAKGLASS
        trimmed = reason.strip()
        if trimmed == "":
            # An empty reason carries no audit value — treat as no-op
            # rather than write a blank comment row.
            return AuditOutcome.NO_BREAKGLASS

        dedup_key = (
            user_id,
            patient_id,
            session_id if session_id is not None else _NO_SESSION_SENTINEL,
        )
        if dedup_key in self._logged_sessions:
            return AuditOutcome.ALREADY_LOGGED

        url = f"{self._base_url}{self._path}"
        body = {
            "user_id": user_id,
            "patient_id": patient_id,
            "reason": trimmed,
        }
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            response.raise_for_status()
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "breakglass-audit write failed; turn proceeds without "
                "audit record",
                extra={
                    "user_id": user_id,
                    "patient_id": patient_id,
                    "exc_type": type(exc).__name__,
                },
            )
            # Do NOT mark as logged — the next turn should retry the
            # write. Eventual consistency beats permanent silence.
            return AuditOutcome.AUDIT_FAILED

        self._logged_sessions.add(dedup_key)
        return AuditOutcome.LOGGED

    async def aclose(self) -> None:
        await self._client.aclose()
