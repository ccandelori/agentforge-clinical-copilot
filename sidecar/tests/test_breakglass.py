"""Tests for the breakglass-audit-logging tool.

The tool fires when a clinician accesses a chart under break-the-glass
intent (``ctx.breakglass_flag = True`` with a ``ctx.breakglass_reason``
text). It writes the reason to OpenEMR's ``log.comments`` via the PHP
internal endpoint and dedups subsequent calls within the same session
so a chart with N tool invocations doesn't produce N audit rows.

The tool MUST NOT raise on transport failures: an audit miss is bad,
but a turn that fails because the audit endpoint is down is worse.
Failures get classified into the :class:`AuditOutcome` enum so the
caller can monitor / alert without having to handle exceptions.
"""

from __future__ import annotations

import httpx

from agentforge.breakglass import AuditOutcome, BreakglassAuditTool
from agentforge.gateway.auth_gateway import RequestContext

BASE_URL = "https://openemr.test"
LOG_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge"
    "/public/internal/log_breakglass.php"
)


def _ctx(
    *,
    breakglass_flag: bool = True,
    breakglass_reason: str | None = "ED after-hours consult",
) -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=7,
        username="dr.smith",
        role="clinician",
        breakglass_flag=breakglass_flag,
        breakglass_reason=breakglass_reason,
        sensitivity_clearances=frozenset(),
        raw_token="user-jwt-xyz",
    )


def _make_tool(handler: httpx.MockTransport) -> BreakglassAuditTool:
    client = httpx.AsyncClient(transport=handler, base_url=BASE_URL)
    return BreakglassAuditTool(base_url=BASE_URL, http_client=client)


# ---------- No-op when breakglass is off ----------


async def test_returns_no_breakglass_when_flag_is_false() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(
        _ctx(breakglass_flag=False), session_id="s1"
    )

    assert outcome == AuditOutcome.NO_BREAKGLASS
    assert called is False


async def test_returns_no_breakglass_when_reason_is_missing() -> None:
    # Defense in depth: a bare "flag=True, reason=None" combo should
    # not produce an audit row with empty comments. The clinical
    # contract is that the reason text is the audit value.
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(
        _ctx(breakglass_reason=None), session_id="s1"
    )

    assert outcome == AuditOutcome.NO_BREAKGLASS
    assert called is False


# ---------- Happy path ----------


async def test_logs_audit_call_with_user_patient_reason() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["method"] = request.method
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(_ctx(), session_id="s1")

    assert outcome == AuditOutcome.LOGGED
    assert captured["url"] == f"{BASE_URL}{LOG_PATH}"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer user-jwt-xyz"
    import json
    body = json.loads(captured["body"])  # type: ignore[arg-type]
    assert body == {
        "user_id": 42,
        "patient_id": 7,
        "reason": "ED after-hours consult",
    }


# ---------- Session dedup ----------


async def test_second_call_with_same_session_is_already_logged() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    first = await tool.log_breakglass_access(_ctx(), session_id="s1")
    second = await tool.log_breakglass_access(_ctx(), session_id="s1")

    assert first == AuditOutcome.LOGGED
    assert second == AuditOutcome.ALREADY_LOGGED
    assert call_count == 1


async def test_different_session_same_user_re_logs() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    await tool.log_breakglass_access(_ctx(), session_id="s1")
    await tool.log_breakglass_access(_ctx(), session_id="s2")

    assert call_count == 2


async def test_different_user_same_session_id_re_logs() -> None:
    # Session IDs are scoped per-user-per-patient by construction
    # (generate_session_id), but defense in depth: dedup keyed
    # explicitly on (user, patient, session) so a session-id collision
    # never lets one user's audit suppress another's.
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    ctx_a = _ctx()
    ctx_b = RequestContext(
        user_id=99,
        patient_id=7,
        username="dr.jones",
        role="clinician",
        breakglass_flag=True,
        breakglass_reason="Coverage for Dr. Smith",
        sensitivity_clearances=frozenset(),
        raw_token="other-jwt",
    )

    await tool.log_breakglass_access(ctx_a, session_id="s1")
    await tool.log_breakglass_access(ctx_b, session_id="s1")

    assert call_count == 2


async def test_session_id_none_uses_per_token_dedup_bucket() -> None:
    # Some turns won't carry a session_id. Two such turns from the same
    # user+patient should still dedup (don't write the audit twice for
    # what is effectively the same authenticated access).
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    await tool.log_breakglass_access(_ctx(), session_id=None)
    await tool.log_breakglass_access(_ctx(), session_id=None)

    assert call_count == 1


# ---------- Failure modes are NOT raised ----------


async def test_500_response_returns_audit_failed_does_not_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "audit logger crashed"})

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(_ctx(), session_id="s1")

    assert outcome == AuditOutcome.AUDIT_FAILED


async def test_failed_audit_does_not_dedup_so_retry_will_try_again() -> None:
    # If the first attempt fails, the dedup bookkeeping should NOT
    # mark the session as logged — otherwise the retry on the next
    # turn would silently skip and we'd never write the audit.
    responses = iter([
        httpx.Response(500, json={"error": "down"}),
        httpx.Response(201, json={"logged": True}),
    ])

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    tool = _make_tool(httpx.MockTransport(handler))

    first = await tool.log_breakglass_access(_ctx(), session_id="s1")
    second = await tool.log_breakglass_access(_ctx(), session_id="s1")

    assert first == AuditOutcome.AUDIT_FAILED
    assert second == AuditOutcome.LOGGED


async def test_network_error_returns_audit_failed_does_not_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(_ctx(), session_id="s1")

    assert outcome == AuditOutcome.AUDIT_FAILED


# ---------- Reason trimming + edge cases ----------


async def test_whitespace_only_reason_is_treated_as_no_breakglass() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    outcome = await tool.log_breakglass_access(
        _ctx(breakglass_reason="   \t  \n"), session_id="s1"
    )

    assert outcome == AuditOutcome.NO_BREAKGLASS
    assert called is False


async def test_reason_is_trimmed_before_send() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(201, json={"logged": True})

    tool = _make_tool(httpx.MockTransport(handler))

    await tool.log_breakglass_access(
        _ctx(breakglass_reason="  Emergency consult  "), session_id="s1"
    )

    import json
    body = json.loads(captured["body"])  # type: ignore[arg-type]
    assert body["reason"] == "Emergency consult"
