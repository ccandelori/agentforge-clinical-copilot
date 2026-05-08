"""CLI tool for daily/weekly Langfuse cost summaries (Week 1 Task #15).

Reads recent generation observations from Langfuse, sums the
``cost_usd`` value carried on each generation's metadata (wired by
Task #14), and prints a readable table grouped by day or by ISO
week.

Run with::

    uv run python -m agentforge.observability.cost_report
    uv run python -m agentforge.observability.cost_report --days 14
    uv run python -m agentforge.observability.cost_report --days 30 --weekly

Where the cost numbers come from
--------------------------------
:meth:`agentforge.observability.langfuse_client.AgentLangfuse.record_llm_call`
attaches ``{"cost_usd": float, "latency_ms": int}`` to the metadata of
each ``llm:<model>`` generation span. This module reads that same key
back from observations of type ``GENERATION``.

Two compatibility paths are supported:

* ``observation.metadata["cost_usd"]`` — the canonical key written by
  this codebase since Task #14.
* ``observation.total_cost`` — Langfuse's own pricing math, used as a
  fallback when an older trace was emitted before Task #14 landed or
  when an external integration writes the standard field instead of
  metadata.

Boundary discipline
-------------------
This is a developer-facing reporting CLI. It does not read any prompt
content or PHI — only generation start times and the ``cost_usd``
float — so it is safe to run from a workstation with read-only
Langfuse credentials.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from agentforge.config import Settings, get_settings

if TYPE_CHECKING:
    # Imported only for typing — the real Langfuse import is deferred to
    # runtime so unit tests for the pure aggregation functions can run
    # without the SDK or credentials.
    from langfuse import Langfuse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure aggregation layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostObservation:
    """One generation's contribution to the cost report.

    The CLI normalizes Langfuse's :class:`ObservationV2` into this minimal
    DTO so the aggregation layer can be tested without standing up the SDK.
    """

    start_time: datetime
    cost_usd: float


def aggregate_costs(observations: Iterable[CostObservation]) -> dict[date, float]:
    """Sum ``cost_usd`` per UTC calendar day.

    Days with no observations are not present in the result. Callers
    that want a contiguous range should fill missing dates themselves
    (see :func:`_iter_day_range`).
    """
    daily: dict[date, float] = defaultdict(float)
    for obs in observations:
        # Normalise to UTC so an observation captured at 23:30 PT and one
        # at 02:30 ET on the same UTC day land in the same bucket.
        utc_ts = obs.start_time.astimezone(UTC)
        daily[utc_ts.date()] += obs.cost_usd
    return dict(daily)


def aggregate_weekly(daily: dict[date, float]) -> dict[date, float]:
    """Roll a per-day map up to per-ISO-week, keyed by the week's Monday.

    ISO weeks start on Monday (``date.isocalendar()`` semantics). The
    returned key is the Monday of the week, so two days from the same
    week always collapse onto the same key regardless of insertion order.
    """
    weekly: dict[date, float] = defaultdict(float)
    for day, cost in daily.items():
        monday = day - timedelta(days=day.weekday())
        weekly[monday] += cost
    return dict(weekly)


def _iter_day_range(start: date, end: date) -> Iterator[date]:
    """Yield every UTC date in ``[start, end]`` inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# ---------------------------------------------------------------------------
# Production spend projection (Task 27.4)
# ---------------------------------------------------------------------------

# Seconds per day, used to scale per-second QPS to per-day spend.
_SECONDS_PER_DAY: int = 86_400

# Days per month for the monthly rollup. 30 is the same convention the
# Anthropic console uses on its monthly-spend forecast — picking 30.44
# (true average) over-rotates a developer-facing projection that callers
# round mentally to the nearest hundred dollars.
_DAYS_PER_MONTH: int = 30


@dataclass(frozen=True)
class ProductionSpendProjection:
    """Projected forward-looking spend for one (cost-per-turn, QPS) pair.

    Returned by :func:`project_production_spend`. Daily and monthly
    figures are USD; the monthly figure is the daily figure scaled by
    ``_DAYS_PER_MONTH``.
    """

    avg_cost_per_turn: float
    projected_qps: float
    daily: float
    monthly: float


def average_cost_per_observation(
    observations: Iterable[CostObservation],
) -> float:
    """Mean ``cost_usd`` across the input list, or 0.0 on empty input.

    Used by the CLI to derive a representative per-turn cost from the
    observed report window before scaling to projected QPS.
    """
    total = 0.0
    count = 0
    for obs in observations:
        total += obs.cost_usd
        count += 1
    if count == 0:
        return 0.0
    return total / count


def project_production_spend(
    *,
    avg_cost_per_turn: float,
    projected_qps: float,
) -> ProductionSpendProjection:
    """Project forward-looking $/day and $/month at the given QPS.

    Math: ``$/day = avg_cost_per_turn × qps × 86_400``; monthly scales
    by 30 days. Both inputs must be non-negative — a "negative QPS"
    is meaningless and a "negative cost per turn" indicates upstream
    aggregation went wrong, both of which we surface rather than
    silently project a refund.
    """
    if avg_cost_per_turn < 0.0:
        raise ValueError(
            f"avg_cost_per_turn must be non-negative; got {avg_cost_per_turn}"
        )
    if projected_qps < 0.0:
        raise ValueError(f"projected_qps must be non-negative; got {projected_qps}")

    daily = avg_cost_per_turn * projected_qps * _SECONDS_PER_DAY
    monthly = daily * _DAYS_PER_MONTH
    return ProductionSpendProjection(
        avg_cost_per_turn=avg_cost_per_turn,
        projected_qps=projected_qps,
        daily=daily,
        monthly=monthly,
    )


def format_projection_report(proj: ProductionSpendProjection) -> str:
    """Render the projection block as a stable text table.

    Consumers (CI cost dashboards, ad-hoc operator runs) parse this by
    line prefix, so the field labels are pinned and the dollar
    formatting matches :func:`format_daily_report`.
    """
    lines = [
        "Production Spend Projection",
        "-" * 30,
        f"Avg $/turn:    ${proj.avg_cost_per_turn:.4f}",
        f"QPS:           {proj.projected_qps:g}",
        f"$/day:         ${proj.daily:.2f}",
        f"$/month:       ${proj.monthly:.2f}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_daily_report(
    daily: dict[date, float],
    *,
    start: date,
    end: date,
) -> str:
    """Render a daily cost table covering ``[start, end]`` inclusive.

    Days with zero recorded cost are still printed (as $0.0000) so the
    operator can tell "no traffic" from "missing data".
    """
    lines = ["Daily Cost Summary", "-" * 30]
    total = 0.0
    for day in _iter_day_range(start, end):
        cost = daily.get(day, 0.0)
        total += cost
        lines.append(f"{day.isoformat()}: ${cost:.4f}")
    lines.append("-" * 30)
    lines.append(f"Total:      ${total:.4f}")
    return "\n".join(lines)


def format_weekly_report(weekly: dict[date, float]) -> str:
    """Render a weekly cost table sorted by week-start Monday."""
    lines = ["Weekly Cost Summary", "-" * 30]
    total = 0.0
    for monday in sorted(weekly):
        cost = weekly[monday]
        total += cost
        lines.append(f"week of {monday.isoformat()}: ${cost:.4f}")
    lines.append("-" * 30)
    lines.append(f"Total:           ${total:.4f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Langfuse fetch layer
# ---------------------------------------------------------------------------


class _ObservationLike(Protocol):
    """Minimal protocol that ``ObservationV2`` satisfies.

    Defined here (instead of importing the SDK type) so tests can hand
    in lightweight stubs without instantiating pydantic models.
    """

    @property
    def start_time(self) -> datetime: ...

    @property
    def metadata(self) -> Any: ...

    @property
    def total_cost(self) -> float | None: ...


def _extract_cost(obs: _ObservationLike) -> float | None:
    """Pull a USD cost off one observation, preferring our metadata key.

    Returns ``None`` when neither path yields a number, so the caller
    can skip generation observations that predate cost accounting
    rather than treating them as $0.
    """
    metadata = obs.metadata
    if isinstance(metadata, dict):
        candidate = metadata.get("cost_usd")
        if isinstance(candidate, int | float):
            return float(candidate)

    if isinstance(obs.total_cost, int | float):
        return float(obs.total_cost)

    return None


def fetch_cost_observations(
    client: Langfuse,
    *,
    start: datetime,
    end: datetime,
    page_limit: int = 100,
) -> list[CostObservation]:
    """Pull all GENERATION observations in ``[start, end]`` and normalise.

    Iterates the cursor-paginated ``observations.get_many`` endpoint
    until the server stops returning a cursor. Only ``metadata`` (for
    ``cost_usd``) and ``usage`` (which carries ``total_cost``) field
    groups are requested to keep the response small.
    """
    out: list[CostObservation] = []
    cursor: str | None = None
    while True:
        response = client.api.observations.get_many(
            type="GENERATION",
            from_start_time=start,
            to_start_time=end,
            limit=page_limit,
            cursor=cursor,
            fields="core,metadata,usage",
        )
        for obs in response.data:
            cost = _extract_cost(obs)
            if cost is None:
                continue
            out.append(CostObservation(start_time=obs.start_time, cost_usd=cost))

        cursor = getattr(response.meta, "cursor", None)
        if not cursor:
            return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost_report",
        description=(
            "Aggregate per-day or per-week LLM cost from Langfuse traces. "
            "Reads cost_usd metadata recorded by record_llm_call."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days back from now to include (default: 7).",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Roll the daily totals up to ISO weeks (Monday-anchored).",
    )
    parser.add_argument(
        "--project-qps",
        type=float,
        default=None,
        help=(
            "Project forward-looking $/day and $/month at the given QPS, "
            "scaled from the observed average cost per turn. Skipped when "
            "absent."
        ),
    )
    return parser


def _build_langfuse_for_report(settings: Settings) -> Langfuse:
    """Construct a read-only Langfuse client for the CLI.

    The CLI doesn't trace anything itself — it just reads — so it
    doesn't need the AgentLangfuse wrapper's HMAC plumbing. A bare
    Langfuse SDK instance is enough, and skipping the wrapper means
    this command doesn't require ``HMAC_KEY`` to be set.
    """
    if not (
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    ):
        raise RuntimeError(
            "Langfuse is not configured. Set LANGFUSE_HOST, "
            "LANGFUSE_PUBLIC_KEY, and LANGFUSE_SECRET_KEY in the "
            "environment before running cost_report."
        )

    from langfuse import Langfuse

    return Langfuse(
        host=settings.langfuse_host,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.days <= 0:
        print("error: --days must be a positive integer", file=sys.stderr)
        return 2

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)

    try:
        settings = get_settings()
        client = _build_langfuse_for_report(settings)
    except Exception as exc:
        # User-facing tool: log the cause for the operator and exit
        # non-zero. Do not echo settings values back — the secret_key
        # could be in there. Catching ``Exception`` (not ``Throwable``)
        # is appropriate here — this is a CLI boundary and we want
        # configuration mistakes, network errors, and SDK exceptions to
        # all map to one clean exit-1 message.
        logger.error("cost_report: failed to connect to Langfuse", exc_info=exc)
        print(f"error: cannot reach Langfuse ({type(exc).__name__})", file=sys.stderr)
        return 1

    try:
        observations = fetch_cost_observations(client, start=start, end=end)
    except Exception as exc:
        logger.error("cost_report: failed to fetch observations", exc_info=exc)
        print(
            f"error: Langfuse query failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1

    daily = aggregate_costs(observations)

    if args.weekly:
        weekly = aggregate_weekly(daily)
        print(format_weekly_report(weekly))
    else:
        print(format_daily_report(daily, start=start.date(), end=end.date()))

    if args.project_qps is not None:
        if args.project_qps < 0.0:
            print(
                "error: --project-qps must be non-negative",
                file=sys.stderr,
            )
            return 2
        avg = average_cost_per_observation(observations)
        proj = project_production_spend(
            avg_cost_per_turn=avg,
            projected_qps=args.project_qps,
        )
        print()
        print(format_projection_report(proj))

    return 0


if __name__ == "__main__":
    sys.exit(main())
