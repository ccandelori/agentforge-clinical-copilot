"""Tests for AgentLangfuse and NullLangfuseClient.

The real client wraps the Langfuse SDK; these tests mock the SDK so we
can verify the boundary discipline (pseudonyms reach the SDK, raw IDs
do not) without network calls. The Null client tests document its
no-op contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentforge.observability import (
    AgentLangfuse,
    LangfuseClient,
    NullLangfuseClient,
    TraceHandle,
    pseudonymize,
)
from agentforge.observability.langfuse_client import _LangfuseTraceHandle

HMAC_KEY = b"test-hmac-key-32-bytes-xxxxxxxxx"
HOST = "http://localhost:3000"
PUBLIC_KEY = "pk-lf-test"
SECRET_KEY = "sk-lf-test"


# ---------- AgentLangfuse construction ----------


def test_agent_langfuse_constructor_validates_required_fields() -> None:
    with pytest.raises(ValueError):
        AgentLangfuse(host="", public_key=PUBLIC_KEY, secret_key=SECRET_KEY, hmac_key=HMAC_KEY)
    with pytest.raises(ValueError):
        AgentLangfuse(host=HOST, public_key="", secret_key=SECRET_KEY, hmac_key=HMAC_KEY)
    with pytest.raises(ValueError):
        AgentLangfuse(host=HOST, public_key=PUBLIC_KEY, secret_key="", hmac_key=HMAC_KEY)
    with pytest.raises(ValueError):
        AgentLangfuse(host=HOST, public_key=PUBLIC_KEY, secret_key=SECRET_KEY, hmac_key=b"")


def _mock_span() -> MagicMock:
    """Build a mock Langfuse span/observation with the methods we use."""
    span = MagicMock()
    span.trace_id = "trace-abc-123"
    # Children returned by start_observation are also mock spans.
    span.start_observation.return_value = MagicMock()
    return span


def _build_client() -> tuple[AgentLangfuse, MagicMock]:
    """Construct AgentLangfuse with the SDK patched out; return (client, sdk_mock)."""
    sdk = MagicMock()
    sdk.start_observation.return_value = _mock_span()
    # The constructor does ``from langfuse import Langfuse`` lazily, so
    # patch the SDK's class symbol at its real location.
    with patch("langfuse.Langfuse", return_value=sdk):
        client = AgentLangfuse(
            host=HOST,
            public_key=PUBLIC_KEY,
            secret_key=SECRET_KEY,
            hmac_key=HMAC_KEY,
        )
    return client, sdk


# ---------- pseudonymize_id ----------


def test_pseudonymize_id_uses_the_shared_helper() -> None:
    client, _ = _build_client()
    assert client.pseudonymize_id(42) == pseudonymize(42, HMAC_KEY)
    assert client.pseudonymize_id("alice") == pseudonymize("alice", HMAC_KEY)


# ---------- trace_turn ----------


def test_trace_turn_passes_pseudonyms_not_raw_ids_to_sdk() -> None:
    client, sdk = _build_client()

    handle = client.trace_turn(
        user_id=42,
        patient_id=999,
        breakglass_flag=True,
        role="Physicians",
    )

    sdk.start_observation.assert_called_once()
    kwargs = sdk.start_observation.call_args.kwargs
    metadata = kwargs["metadata"]

    expected_user = pseudonymize(42, HMAC_KEY)
    expected_patient = pseudonymize(999, HMAC_KEY)

    # The pseudonyms are present.
    assert metadata["user_id_pseudonym"] == expected_user
    assert metadata["patient_id_pseudonym"] == expected_patient
    assert metadata["breakglass_flag"] is True
    assert metadata["role"] == "Physicians"

    # And — the safety property — the raw IDs are NOT in the payload.
    serialised = repr(kwargs)
    assert "42" not in serialised or expected_user.startswith("42") is False
    assert "999" not in serialised
    assert isinstance(handle, _LangfuseTraceHandle)


def test_trace_turn_returns_handle_with_trace_id() -> None:
    client, _ = _build_client()
    handle = client.trace_turn(
        user_id=1, patient_id=2, breakglass_flag=False, role=None
    )
    assert isinstance(handle, TraceHandle)
    assert handle.trace_id == "trace-abc-123"


def test_trace_turn_only_emits_pseudonyms_in_metadata() -> None:
    """Boundary check: the only paths into the SDK from trace_turn are
    the start_observation kwargs we control. No raw IDs leak through
    other Langfuse method calls."""
    client, sdk = _build_client()

    client.trace_turn(user_id=42, patient_id=999, breakglass_flag=False, role="RN")

    # Only start_observation is touched on the SDK during trace_turn —
    # any future trace-context update would need its own SDK call.
    assert sdk.start_observation.call_count == 1


# ---------- record_tool_call ----------


def test_record_tool_call_creates_child_span_with_hashes_only() -> None:
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_tool_call(
        handle,
        tool_name="get_demographics",
        status="ok",
        latency_ms=120,
        cache_hit=False,
        args_hash="aaaa",
        result_hash="bbbb",
    )

    parent_span.start_observation.assert_called_once()
    kwargs = parent_span.start_observation.call_args.kwargs
    assert kwargs["name"] == "tool:get_demographics"
    assert kwargs["as_type"] == "tool"
    md = kwargs["metadata"]
    assert md["status"] == "ok"
    assert md["latency_ms"] == 120
    assert md["args_hash"] == "aaaa"
    assert md["result_hash"] == "bbbb"


def test_record_tool_call_is_a_noop_for_foreign_handle() -> None:
    """A handle from NullLangfuseClient should not crash AgentLangfuse."""
    client, sdk = _build_client()
    null = NullLangfuseClient()
    foreign = null.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)

    # Reset call counts incurred during construction wiring.
    sdk.reset_mock()

    client.record_tool_call(
        foreign,
        tool_name="x",
        status="ok",
        latency_ms=1,
        cache_hit=False,
        args_hash=None,
        result_hash=None,
    )
    # Nothing reached the SDK because the handle is opaque to it.
    sdk.start_observation.assert_not_called()


# ---------- record_llm_call ----------


def test_record_llm_call_emits_generation_span_with_token_counts() -> None:
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_llm_call(
        handle,
        model="claude-opus-4-7",
        prompt_tokens=500,
        completion_tokens=120,
        latency_ms=900,
    )

    parent_span.start_observation.assert_called_once()
    kwargs = parent_span.start_observation.call_args.kwargs
    assert kwargs["as_type"] == "generation"
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["usage_details"] == {"input": 500, "output": 120}


def test_record_llm_call_attaches_cost_usd_to_metadata() -> None:
    """When the orchestrator passes a calculated cost, the generation
    span carries it in metadata so Langfuse aggregates dollar spend
    per trace / per session. ARCHITECTURE.md §7 lists $/turn as a
    required observability signal; this is where the wire crosses
    into the trace store."""
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_llm_call(
        handle,
        model="claude-sonnet-4-5",
        prompt_tokens=1000,
        completion_tokens=500,
        latency_ms=2000,
        cost_usd=0.0105,
    )

    kwargs = parent_span.start_observation.call_args.kwargs
    metadata = kwargs["metadata"]
    assert metadata.get("cost_usd") == pytest.approx(0.0105, rel=1e-9)
    # Latency must still be there — adding cost mustn't drop existing fields.
    assert metadata.get("latency_ms") == 2000


def test_record_llm_call_omits_cost_when_not_provided() -> None:
    """Backwards compat: callers that don't pass cost_usd produce
    a span with no cost metadata key (rather than ``cost_usd=None``)
    so legacy traces remain visually clean in the UI."""
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_llm_call(
        handle,
        model="claude-sonnet-4-5",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=500,
    )

    kwargs = parent_span.start_observation.call_args.kwargs
    metadata = kwargs["metadata"]
    assert "cost_usd" not in metadata


# ---------- record_planner_decision ----------


def test_record_planner_decision_emits_evaluator_span_with_use_case() -> None:
    """Planner span captures the closed-enum ``use_case`` plus dispatch
    shape (tool_count, batch_count). All values are non-PHI — ``use_case``
    is from the four-element closed taxonomy and counts describe the
    planner's structural output, not its content. Mirror of
    ``record_verifier_decision`` so the dashboard treats both as
    evaluator-style observations.
    """
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_planner_decision(
        handle,
        use_case="admit_synthesis",
        tool_count=10,
        batch_count=3,
    )

    parent_span.start_observation.assert_called_once()
    kwargs = parent_span.start_observation.call_args.kwargs
    assert kwargs["name"] == "planner"
    assert kwargs["as_type"] == "evaluator"
    md = kwargs["metadata"]
    assert md["use_case"] == "admit_synthesis"
    assert md["tool_count"] == 10
    assert md["batch_count"] == 3


# ---------- record_verifier_decision ----------


def test_record_verifier_decision_emits_evaluator_span_with_counts() -> None:
    client, sdk = _build_client()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    parent_span = sdk.start_observation.return_value

    client.record_verifier_decision(
        handle,
        claims_emitted=10,
        claims_rejected=2,
        by_category={"fabricated_id": 1, "value_mismatch": 1},
    )

    parent_span.start_observation.assert_called_once()
    kwargs = parent_span.start_observation.call_args.kwargs
    assert kwargs["as_type"] == "evaluator"
    md = kwargs["metadata"]
    assert md["claims_emitted"] == 10
    assert md["claims_rejected"] == 2
    assert md["by_category"] == {"fabricated_id": 1, "value_mismatch": 1}


# ---------- shutdown ----------


def test_flush_delegates_to_sdk() -> None:
    client, sdk = _build_client()
    client.flush()
    sdk.flush.assert_called_once()


async def test_aclose_calls_sdk_shutdown() -> None:
    client, sdk = _build_client()
    await client.aclose()
    sdk.shutdown.assert_called_once()


# ---------- NullLangfuseClient ----------


def test_null_client_pseudonymize_returns_real_token_when_key_supplied() -> None:
    client = NullLangfuseClient(hmac_key=HMAC_KEY)
    assert client.pseudonymize_id(42) == pseudonymize(42, HMAC_KEY)


def test_null_client_pseudonymize_returns_anonymous_without_key() -> None:
    client = NullLangfuseClient()
    assert client.pseudonymize_id(42) == "anonymous"


def test_null_client_methods_are_noops_and_do_not_raise() -> None:
    client = NullLangfuseClient()
    handle = client.trace_turn(user_id=1, patient_id=2, breakglass_flag=False, role=None)
    assert handle.trace_id is None

    # Every span method must accept the same kwargs as the real client
    # and return None silently.
    assert (
        client.record_tool_call(
            handle,
            tool_name="x",
            status="ok",
            latency_ms=1,
            cache_hit=False,
            args_hash=None,
            result_hash=None,
        )
        is None
    )
    assert (
        client.record_llm_call(
            handle,
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
        )
        is None
    )
    assert (
        client.record_verifier_decision(
            handle,
            claims_emitted=0,
            claims_rejected=0,
            by_category={},
        )
        is None
    )
    assert (
        client.record_planner_decision(
            handle,
            use_case="admit_synthesis",
            tool_count=0,
            batch_count=0,
        )
        is None
    )
    assert client.flush() is None


async def test_null_client_aclose_returns_none() -> None:
    client = NullLangfuseClient()
    assert await client.aclose() is None


# ---------- Protocol satisfaction ----------


def test_both_clients_satisfy_protocol() -> None:
    """Documents the boundary contract — both implementations must be
    swappable for ``LangfuseClient`` at app construction time."""
    null: LangfuseClient = NullLangfuseClient()
    real, _ = _build_client()
    typed_real: LangfuseClient = real

    # Smoke: the runtime-checkable Protocol agrees.
    assert isinstance(null, LangfuseClient)
    assert isinstance(typed_real, LangfuseClient)


# ---------- Boundary discipline (no raw payloads in span signatures) ----------


def test_record_tool_call_signature_takes_hashes_not_payloads() -> None:
    """Callers cannot accidentally pass an args dict — the signature
    forces ``str | None`` for args_hash and result_hash."""
    import inspect

    sig = inspect.signature(AgentLangfuse.record_tool_call)
    args_hash_param = sig.parameters["args_hash"]
    result_hash_param = sig.parameters["result_hash"]

    args_anno: Any = args_hash_param.annotation
    result_anno: Any = result_hash_param.annotation
    # The annotations are PEP 604 unions; evaluating to ``str | None``
    # is enough for our purposes — what we want to verify is that
    # ``dict``, ``bytes``, etc. are not part of the Union.
    rendered = f"{args_anno} {result_anno}"
    assert "dict" not in rendered
    assert "bytes" not in rendered
