"""Per-call cost calculation for LLM usage (Week 1 Task #14).

ARCHITECTURE.md §7 lists $/turn as a required observability signal.
This module is the closed-form pricing layer: a static table of
``$/token`` rates per model and a single ``calculate_cost`` function
that takes a (model, input_tokens, output_tokens) triple and returns
the dollar cost.

Pricing notes
-------------
Rates below were sourced from Anthropic's published pricing page as of
2026-05. They are floats (not :class:`decimal.Decimal`) because:

* The orchestrator never settles invoices; this is a developer-facing
  observability number.
* Float math at <$10/turn precision is well within the noise of
  Anthropic's own usage rounding.

Update the table when Anthropic publishes new prices or when this
project starts shipping new models. A missing entry returns 0.0 and
emits one warning per (process, model) pair so the operator notices
without spam in the trace store.

Cost values flow into Langfuse generation events via
``LangfuseClient.record_llm_call(cost_usd=...)`` and into the
HTTP response on ``/agentforge/turn`` as ``X-Agent-Cost-USD``.

Phase B
-------
The streaming-path equivalent (``LLMClient.stream``) lives in
Tasks #9-#13 and will reuse this same module — no API changes
expected, just a new call site. See Week 1 PRD for the wiring plan.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

# Anthropic's published image-token formula (per docs/vision page):
# tokens ≈ (width * height) / 750. We round to the nearest integer
# rather than truncating because the reference implementation in
# Anthropic's pricing calculator rounds, and a round-vs-floor delta
# of 1 token at 1024×1024 is the difference between $0.0010657 and
# $0.0010665 — well below display precision but reproducible.
_IMAGE_TOKEN_DIVISOR: Final[float] = 750.0

# Pattern for dated model aliases ("claude-haiku-4-5-20251001"). The
# prefix matches a base model in PRICING; the trailing "-YYYYMMDD"
# is Anthropic's canonical date-stamped pin. Stripping the suffix lets
# the operator pin a dated alias in env (which is the recommended
# practice for production) without us having to duplicate the rate row
# for every release date.
_DATED_ALIAS_SUFFIX: Final[re.Pattern[str]] = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class _Rates:
    """USD per token for one model.

    Stored as $ per single token — multiplying by token counts gives
    cost directly without an extra "/M" division on the hot path.
    """

    input: float
    output: float


# Per-model rates as of 2026-05. Add a new entry on model rollout;
# never delete (it makes historical traces uninterpretable).
PRICING: Final[dict[str, _Rates]] = {
    # Claude 4.x family — Anthropic's primary offering as of 2026-05.
    "claude-sonnet-4-5": _Rates(input=3.0e-6, output=15.0e-6),
    "claude-sonnet-4-6": _Rates(input=3.0e-6, output=15.0e-6),
    "claude-haiku-4-5": _Rates(input=0.80e-6, output=4.0e-6),
    # Opus is the high-context, premium-quality option. Pricing is
    # the published 1M-context tier — most expensive in the catalog.
    "claude-opus-4-7": _Rates(input=15.0e-6, output=75.0e-6),
    "claude-opus-4-7-1m": _Rates(input=15.0e-6, output=75.0e-6),
    # Legacy 3.5 entries kept so historical traces still resolve.
    "claude-3-5-sonnet": _Rates(input=3.0e-6, output=15.0e-6),
    "claude-3-5-haiku": _Rates(input=0.80e-6, output=4.0e-6),
}

# Process-local memo of unknown models we've already warned about.
# Each (process, model) pair logs once; subsequent calls return 0.0
# silently so a misconfigured ``model`` env var doesn't drown the
# trace store.
_warned_unknown_models: set[str] = set()


def _resolve_rates(model: str) -> _Rates | None:
    """Look up a rate row, accepting Anthropic-style dated aliases.

    Returns the row matching ``model`` directly, or — if the model
    name carries a trailing ``-YYYYMMDD`` Anthropic dated suffix — the
    row for the un-stamped base model. This lets operators pin a
    dated alias in env (recommended for production) without the
    pricing table needing an entry per release date.
    """
    rates = PRICING.get(model)
    if rates is not None:
        return rates
    base = _DATED_ALIAS_SUFFIX.sub("", model)
    if base != model:
        return PRICING.get(base)
    return None


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost of one LLM call.

    A model not present in :data:`PRICING` returns 0.0 and emits one
    warning the first time it's seen this process. The function never
    raises — calling it can't take down a turn. Dated Anthropic aliases
    (``claude-haiku-4-5-20251001``) resolve back to the base model row.
    """
    rates = _resolve_rates(model)
    if rates is None:
        if model not in _warned_unknown_models:
            _warned_unknown_models.add(model)
            logger.warning(
                "calculate_cost: no pricing entry for model %r; "
                "returning 0.0. Add an entry to PRICING in "
                "agentforge.observability.cost when this model "
                "ships to production.",
                model,
            )
        return 0.0
    return input_tokens * rates.input + output_tokens * rates.output


def estimate_image_tokens(*, width: int, height: int) -> int:
    """Estimate Anthropic image tokens for one rendered page.

    Mirrors Anthropic's published formula: ``tokens ≈ (width * height) / 750``.
    Result is rounded to the nearest integer. Both dimensions must be
    positive — passing zero or negative values is a programmer error
    (the renderer never emits a 0×N pixmap), so we raise rather than
    silently coerce.
    """
    if width <= 0 or height <= 0:
        raise ValueError(
            f"image dimensions must be positive; got width={width}, height={height}"
        )
    return round((width * height) / _IMAGE_TOKEN_DIVISOR)


def calculate_vision_cost(
    *,
    model: str,
    text_input_tokens: int,
    image_dimensions: list[tuple[int, int]],
    output_tokens: int,
) -> float:
    """Return the USD cost of one Anthropic vision call.

    Anthropic prices vision and text input at the same per-token rate;
    the only special case is converting image pixel area into a token
    estimate via :func:`estimate_image_tokens` so the pricing math
    works the same way for both modalities.

    The total input-token bill is ``text_input_tokens + Σ image_tokens``;
    the output rate applies unchanged. As with :func:`calculate_cost`,
    an unknown model logs once and returns 0.0 — a misconfigured
    ``ANTHROPIC_VISION_MODEL`` cannot take down a turn.
    """
    rates = _resolve_rates(model)
    if rates is None:
        if model not in _warned_unknown_models:
            _warned_unknown_models.add(model)
            logger.warning(
                "calculate_vision_cost: no pricing entry for model %r; "
                "returning 0.0. Add an entry to PRICING in "
                "agentforge.observability.cost when this vision model "
                "ships to production.",
                model,
            )
        return 0.0

    image_tokens = sum(
        estimate_image_tokens(width=w, height=h) for w, h in image_dimensions
    )
    total_input = text_input_tokens + image_tokens
    return total_input * rates.input + output_tokens * rates.output


@dataclass(frozen=True)
class CallCost:
    """One LLM call's accounting record.

    Captured at the call site (orchestrator) so the per-turn aggregator
    can sum across all calls in a turn and the Langfuse instrumentation
    can attach the cost to the matching generation span.
    """

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


__all__ = [
    "PRICING",
    "CallCost",
    "calculate_cost",
    "calculate_vision_cost",
    "estimate_image_tokens",
]
