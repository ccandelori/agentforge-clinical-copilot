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


# ---------------------------------------------------------------------------
# Vision-call pricing (Task 27.1)
# ---------------------------------------------------------------------------


def test_estimate_image_tokens_uses_anthropic_formula() -> None:
    """Anthropic's published rule: image tokens ≈ (width * height) / 750,
    rounded to the nearest integer. A 1500×1000 PNG → 2000 tokens.
    """
    from agentforge.observability.cost import estimate_image_tokens

    # 1500 * 1000 = 1_500_000; / 750 = 2000.
    assert estimate_image_tokens(width=1500, height=1000) == 2000


def test_estimate_image_tokens_rounds_to_nearest_integer() -> None:
    from agentforge.observability.cost import estimate_image_tokens

    # 800 * 600 = 480_000; / 750 = 640. Exact.
    assert estimate_image_tokens(width=800, height=600) == 640
    # 1000 * 1000 = 1_000_000; / 750 ≈ 1333.33 → rounds to 1333.
    assert estimate_image_tokens(width=1000, height=1000) == 1333


def test_estimate_image_tokens_rejects_nonpositive_dimensions() -> None:
    from agentforge.observability.cost import estimate_image_tokens

    with pytest.raises(ValueError):
        estimate_image_tokens(width=0, height=100)
    with pytest.raises(ValueError):
        estimate_image_tokens(width=100, height=-1)


def test_calculate_vision_cost_known_haiku_model() -> None:
    """Haiku 4.5 vision call: text tokens + summed image tokens, all
    priced at the standard input rate ($0.80/M); output at $4/M.

    Two 1500×1000 images = 4000 image tokens. Plus 200 text tokens =
    4200 input tokens. 200 output tokens.
    Cost = 4200 * 0.80e-6 + 200 * 4e-6 = 0.00336 + 0.0008 = 0.00416.
    """
    from agentforge.observability.cost import calculate_vision_cost

    cost = calculate_vision_cost(
        model="claude-haiku-4-5-20251001",
        text_input_tokens=200,
        image_dimensions=[(1500, 1000), (1500, 1000)],
        output_tokens=200,
    )
    assert cost == pytest.approx(0.00416, rel=1e-9)


def test_calculate_vision_cost_droplet_haiku_alias_resolves() -> None:
    """Production droplet pins ``claude-haiku-4-5-20251001`` (dated
    alias). The pricing table should resolve dated aliases back to the
    base ``claude-haiku-4-5`` rate row so the operator doesn't have to
    duplicate every dated alias.
    """
    from agentforge.observability.cost import calculate_vision_cost

    cost = calculate_vision_cost(
        model="claude-haiku-4-5-20251001",
        text_input_tokens=0,
        image_dimensions=[(750, 750)],
        output_tokens=0,
    )
    # 750*750/750 = 750 image tokens at 0.80e-6 = 0.0006.
    assert cost == pytest.approx(0.0006, rel=1e-9)


def test_calculate_vision_cost_no_images() -> None:
    """A vision-shaped call with zero images degenerates to the same
    price as ``calculate_cost`` over the same text/output tokens.
    """
    from agentforge.observability.cost import calculate_cost, calculate_vision_cost

    vision = calculate_vision_cost(
        model="claude-sonnet-4-5",
        text_input_tokens=1000,
        image_dimensions=[],
        output_tokens=500,
    )
    plain = calculate_cost("claude-sonnet-4-5", 1000, 500)
    assert vision == pytest.approx(plain, rel=1e-9)


def test_calculate_vision_cost_unknown_model_returns_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirror ``calculate_cost``'s soft-fail contract for vision pricing —
    one warning per process, no crash on a misconfigured model.
    """
    from agentforge.observability.cost import calculate_vision_cost

    with caplog.at_level(logging.WARNING):
        cost = calculate_vision_cost(
            model="claude-vision-mystery-9000",
            text_input_tokens=100,
            image_dimensions=[(800, 600)],
            output_tokens=50,
        )
    assert cost == 0.0


def test_pricing_table_includes_dated_haiku_alias() -> None:
    """Production env pin ``claude-haiku-4-5-20251001`` must resolve
    against the pricing table so vision calls book against a real rate.
    """
    from agentforge.observability.cost import calculate_cost

    # 1000 input * 0.80e-6 + 500 output * 4e-6 = 0.0008 + 0.002 = 0.0028.
    assert calculate_cost(
        "claude-haiku-4-5-20251001", 1000, 500
    ) == pytest.approx(0.0028, rel=1e-9)
