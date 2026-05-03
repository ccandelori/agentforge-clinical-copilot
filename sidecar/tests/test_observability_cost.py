"""Tests for the cost calculation module (Week 1 Task #14).

Pure unit tests — no I/O, no fixtures. Pricing is a static table; the
calculate_cost function is a closed-form computation. The properties
worth pinning here:

  * Known model returns (input_tokens * input_rate + output_tokens *
    output_rate) within float-precision tolerance.
  * Unknown model returns 0.0 and emits a single warning per process
    (not per call) so a misconfigured ``model`` doesn't drown the
    log.
  * 0 + 0 tokens always returns 0.0 regardless of model.
  * CallCost is frozen — mutating attributes raises.
"""

from __future__ import annotations

import logging

import pytest


def test_calculate_cost_known_model_sonnet() -> None:
    """Anthropic claude-sonnet-4-5 published at $3/M input, $15/M output.
    1000 input + 500 output tokens -> 0.003 + 0.0075 = 0.0105."""
    from agentforge.observability.cost import calculate_cost

    cost = calculate_cost("claude-sonnet-4-5", 1000, 500)
    assert cost == pytest.approx(0.0105, rel=1e-9)


def test_calculate_cost_known_model_haiku() -> None:
    """claude-haiku-4-5 at $0.80/M input, $4/M output.
    10_000 input + 2000 output -> 0.008 + 0.008 = 0.016."""
    from agentforge.observability.cost import calculate_cost

    cost = calculate_cost("claude-haiku-4-5", 10_000, 2000)
    assert cost == pytest.approx(0.016, rel=1e-9)


def test_calculate_cost_zero_tokens_returns_zero() -> None:
    from agentforge.observability.cost import calculate_cost

    assert calculate_cost("claude-sonnet-4-5", 0, 0) == 0.0


def test_calculate_cost_unknown_model_returns_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown model shouldn't crash a turn. Returns 0.0 and emits
    exactly one warning (not one-per-call) so the operator notices
    once without log spam.
    """
    from agentforge.observability.cost import calculate_cost

    with caplog.at_level(logging.WARNING):
        cost1 = calculate_cost("claude-future-model-9000", 1000, 500)
        cost2 = calculate_cost("claude-future-model-9000", 2000, 1000)

    assert cost1 == 0.0
    assert cost2 == 0.0
    # Single warning regardless of how many calls hit the unknown model.
    warnings = [
        r for r in caplog.records
        if "claude-future-model-9000" in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"Expected exactly one warning for repeated unknown-model "
        f"calls; got {len(warnings)}: {[r.getMessage() for r in warnings]}"
    )


def test_call_cost_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    from agentforge.observability.cost import CallCost

    cost = CallCost(
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001050,
    )
    with pytest.raises(FrozenInstanceError):
        cost.cost_usd = 999.99  # type: ignore[misc]


def test_call_cost_equality_by_value() -> None:
    """Two CallCost instances with identical fields compare equal —
    needed for orchestrator-level aggregation tests."""
    from agentforge.observability.cost import CallCost

    a = CallCost(model="m", input_tokens=1, output_tokens=2, cost_usd=0.001)
    b = CallCost(model="m", input_tokens=1, output_tokens=2, cost_usd=0.001)
    assert a == b
