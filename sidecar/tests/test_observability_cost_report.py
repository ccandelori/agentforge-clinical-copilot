"""Tests for the cost_report CLI tool (Week 1 Task #15).

The module is split into a pure aggregation layer (datetime → date
buckets, daily → weekly rollup, formatting) and a thin Langfuse-fetch
adapter. Most of the surface area can be exercised without the SDK
or any network access; the few tests that need the client use a stub.

Properties pinned here:

  * ``aggregate_costs`` sums correctly across the day boundary in UTC.
  * ``aggregate_weekly`` collapses ISO weeks onto Monday-anchored keys.
  * The CLI ``--days N`` flag drives the report window.
  * Output formatting is stable (table headers, dollar precision).
  * Misconfigured Langfuse → exit 1 with a non-zero code, no traceback.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# aggregate_costs
# ---------------------------------------------------------------------------


def test_aggregate_costs_sums_within_a_day() -> None:
    """Three generations on the same UTC date → one bucket with the sum."""
    from agentforge.observability.cost_report import (
        CostObservation,
        aggregate_costs,
    )

    obs = [
        CostObservation(datetime(2026, 5, 1, 9, 0, tzinfo=UTC), 0.0010),
        CostObservation(datetime(2026, 5, 1, 12, 30, tzinfo=UTC), 0.0025),
        CostObservation(datetime(2026, 5, 1, 23, 59, tzinfo=UTC), 0.0005),
    ]
    daily = aggregate_costs(obs)
    assert daily == {date(2026, 5, 1): pytest.approx(0.0040, rel=1e-9)}


def test_aggregate_costs_separates_distinct_days() -> None:
    from agentforge.observability.cost_report import (
        CostObservation,
        aggregate_costs,
    )

    obs = [
        CostObservation(datetime(2026, 5, 1, 9, 0, tzinfo=UTC), 0.10),
        CostObservation(datetime(2026, 5, 2, 9, 0, tzinfo=UTC), 0.20),
        CostObservation(datetime(2026, 5, 3, 9, 0, tzinfo=UTC), 0.30),
    ]
    daily = aggregate_costs(obs)
    assert daily == {
        date(2026, 5, 1): pytest.approx(0.10, rel=1e-9),
        date(2026, 5, 2): pytest.approx(0.20, rel=1e-9),
        date(2026, 5, 3): pytest.approx(0.30, rel=1e-9),
    }


def test_aggregate_costs_normalises_non_utc_timestamps_to_utc_date() -> None:
    """An observation timestamped at 23:30 PT (= 06:30 UTC next day)
    should land in the UTC-next-day bucket, not the local-day bucket.
    """
    from datetime import timezone

    from agentforge.observability.cost_report import (
        CostObservation,
        aggregate_costs,
    )

    pt = timezone(timedelta(hours=-7))  # PDT
    obs = [
        # 2026-05-01 23:30 PDT = 2026-05-02 06:30 UTC
        CostObservation(datetime(2026, 5, 1, 23, 30, tzinfo=pt), 0.05),
    ]
    daily = aggregate_costs(obs)
    assert daily == {date(2026, 5, 2): pytest.approx(0.05, rel=1e-9)}


def test_aggregate_costs_empty_input_returns_empty_dict() -> None:
    from agentforge.observability.cost_report import aggregate_costs

    assert aggregate_costs([]) == {}


# ---------------------------------------------------------------------------
# aggregate_weekly
# ---------------------------------------------------------------------------


def test_aggregate_weekly_collapses_iso_week_onto_monday() -> None:
    """ISO week starts Monday. 2026-05-04 is a Monday; 05-04..05-10 all
    roll into the 2026-05-04 bucket.
    """
    from agentforge.observability.cost_report import aggregate_weekly

    daily = {
        date(2026, 5, 4): 0.10,  # Monday
        date(2026, 5, 6): 0.20,  # Wednesday
        date(2026, 5, 10): 0.30,  # Sunday
    }
    weekly = aggregate_weekly(daily)
    assert weekly == {date(2026, 5, 4): pytest.approx(0.60, rel=1e-9)}


def test_aggregate_weekly_separates_adjacent_weeks() -> None:
    from agentforge.observability.cost_report import aggregate_weekly

    daily = {
        date(2026, 5, 10): 1.00,  # Sunday — week of 05-04
        date(2026, 5, 11): 2.00,  # Monday — week of 05-11
    }
    weekly = aggregate_weekly(daily)
    assert weekly == {
        date(2026, 5, 4): pytest.approx(1.00, rel=1e-9),
        date(2026, 5, 11): pytest.approx(2.00, rel=1e-9),
    }


# ---------------------------------------------------------------------------
# Cost extraction (metadata + total_cost compatibility)
# ---------------------------------------------------------------------------


@dataclass
class _StubObservation:
    """Mimics the shape of langfuse.api.commons.types.observation_v2.ObservationV2.

    Only the attributes the cost_report module reads are present.
    """

    start_time: datetime
    metadata: Any = None
    total_cost: float | None = None


def test_extract_cost_prefers_metadata_cost_usd() -> None:
    from agentforge.observability.cost_report import _extract_cost

    obs = _StubObservation(
        start_time=datetime(2026, 5, 1, tzinfo=UTC),
        metadata={"cost_usd": 0.0042, "latency_ms": 120},
        total_cost=999.0,  # should be ignored when metadata wins
    )
    assert _extract_cost(obs) == pytest.approx(0.0042, rel=1e-9)


def test_extract_cost_falls_back_to_total_cost() -> None:
    """Older traces (pre-Task #14) wrote total_cost via Langfuse pricing
    but never set our metadata key. We should still count those.
    """
    from agentforge.observability.cost_report import _extract_cost

    obs = _StubObservation(
        start_time=datetime(2026, 5, 1, tzinfo=UTC),
        metadata={"latency_ms": 120},  # no cost_usd
        total_cost=0.0017,
    )
    assert _extract_cost(obs) == pytest.approx(0.0017, rel=1e-9)


def test_extract_cost_returns_none_when_no_signal() -> None:
    from agentforge.observability.cost_report import _extract_cost

    obs = _StubObservation(
        start_time=datetime(2026, 5, 1, tzinfo=UTC),
        metadata=None,
        total_cost=None,
    )
    assert _extract_cost(obs) is None


# ---------------------------------------------------------------------------
# fetch_cost_observations: pagination + filtering
# ---------------------------------------------------------------------------


@dataclass
class _StubResponse:
    data: list[_StubObservation]
    meta: Any


@dataclass
class _StubMeta:
    cursor: str | None


class _StubObservationsApi:
    """Hand-rolled stand-in for ``Langfuse.api.observations``."""

    def __init__(self, pages: list[list[_StubObservation]]) -> None:
        self._pages = pages
        self.calls: list[dict[str, Any]] = []

    def get_many(self, **kwargs: Any) -> _StubResponse:
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        if idx >= len(self._pages):
            return _StubResponse(data=[], meta=_StubMeta(cursor=None))
        page = self._pages[idx]
        next_cursor = f"cursor-{idx + 1}" if idx + 1 < len(self._pages) else None
        return _StubResponse(data=page, meta=_StubMeta(cursor=next_cursor))


class _StubLangfuseClient:
    def __init__(self, observations_api: _StubObservationsApi) -> None:
        self.api = type("_Api", (), {"observations": observations_api})()


def test_fetch_cost_observations_paginates_until_cursor_exhausted() -> None:
    from agentforge.observability.cost_report import fetch_cost_observations

    page1 = [
        _StubObservation(
            start_time=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
            metadata={"cost_usd": 0.10},
        ),
        _StubObservation(
            start_time=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            metadata={"cost_usd": 0.20},
        ),
    ]
    page2 = [
        _StubObservation(
            start_time=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
            metadata={"cost_usd": 0.30},
        ),
    ]
    api = _StubObservationsApi(pages=[page1, page2])
    client = _StubLangfuseClient(api)

    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 5, 7, tzinfo=UTC)
    out = fetch_cost_observations(client, start=start, end=end)  # type: ignore[arg-type]

    assert [round(o.cost_usd, 4) for o in out] == [0.10, 0.20, 0.30]
    # First call has no cursor; second call passes the cursor returned
    # by the first response.
    assert api.calls[0]["cursor"] is None
    assert api.calls[1]["cursor"] == "cursor-1"
    # Both calls should target GENERATION observations within the window.
    for call in api.calls:
        assert call["type"] == "GENERATION"
        assert call["from_start_time"] == start
        assert call["to_start_time"] == end


def test_fetch_cost_observations_skips_observations_with_no_cost() -> None:
    from agentforge.observability.cost_report import fetch_cost_observations

    page = [
        _StubObservation(
            start_time=datetime(2026, 5, 1, 9, 0, tzinfo=UTC),
            metadata={"cost_usd": 0.10},
        ),
        _StubObservation(
            start_time=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
            metadata=None,  # legacy generation, no cost recorded
        ),
    ]
    api = _StubObservationsApi(pages=[page])
    client = _StubLangfuseClient(api)

    out = fetch_cost_observations(
        client,  # type: ignore[arg-type]
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 7, tzinfo=UTC),
    )
    assert len(out) == 1
    assert out[0].cost_usd == pytest.approx(0.10, rel=1e-9)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_format_daily_report_includes_zero_days_in_range() -> None:
    """A day with no recorded cost should still appear so the operator
    can distinguish 'no traffic' from 'data missing'.
    """
    from agentforge.observability.cost_report import format_daily_report

    daily = {date(2026, 5, 1): 0.10, date(2026, 5, 3): 0.30}
    out = format_daily_report(
        daily,
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert "Daily Cost Summary" in out
    assert "2026-05-01: $0.1000" in out
    assert "2026-05-02: $0.0000" in out  # zero-day fill
    assert "2026-05-03: $0.3000" in out
    assert "Total:      $0.4000" in out


def test_format_weekly_report_sorts_by_week_monday() -> None:
    from agentforge.observability.cost_report import format_weekly_report

    weekly = {
        date(2026, 5, 11): 2.00,
        date(2026, 5, 4): 1.00,
    }
    out = format_weekly_report(weekly)
    lines = out.splitlines()
    # First non-header data line should be the earlier week.
    week_lines = [line for line in lines if line.startswith("week of ")]
    assert week_lines[0].startswith("week of 2026-05-04: $1.0000")
    assert week_lines[1].startswith("week of 2026-05-11: $2.0000")
    assert "Total:           $3.0000" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_settings() -> Iterator[None]:
    """Pretend Langfuse is configured so _build_langfuse_for_report
    doesn't short-circuit. The Langfuse import itself is patched out.
    """
    fake_settings = type(
        "_FakeSettings",
        (),
        {
            "langfuse_host": "https://langfuse.example",
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
        },
    )()
    with patch("agentforge.observability.cost_report.get_settings", return_value=fake_settings):
        yield


def test_cli_days_flag_drives_window_size(
    patched_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--days 7`` should produce 7 day-rows in the table."""
    from agentforge.observability import cost_report

    captured_kwargs: dict[str, Any] = {}

    def fake_fetch(
        client: Any,
        *,
        start: datetime,
        end: datetime,
        page_limit: int = 100,
    ) -> list[Any]:
        captured_kwargs["start"] = start
        captured_kwargs["end"] = end
        return []

    with (
        patch.object(cost_report, "_build_langfuse_for_report", return_value=object()),
        patch.object(cost_report, "fetch_cost_observations", side_effect=fake_fetch),
    ):
        rc = cost_report.main(["--days", "7"])

    assert rc == 0
    delta = captured_kwargs["end"] - captured_kwargs["start"]
    assert delta == timedelta(days=7)

    out = capsys.readouterr().out
    # Default daily report: header + 8 dates (start..end inclusive when
    # end-start=7 days produces 8 calendar rows) + footer + total.
    assert "Daily Cost Summary" in out
    date_lines = [line for line in out.splitlines() if line.startswith("20")]
    # Inclusive range of 7 days back through today = 8 rows.
    assert len(date_lines) == 8
    assert "Total:" in out


def test_cli_rejects_zero_or_negative_days(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agentforge.observability import cost_report

    rc = cost_report.main(["--days", "0"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "must be a positive integer" in err


def test_cli_weekly_flag_emits_weekly_table(
    patched_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agentforge.observability import cost_report
    from agentforge.observability.cost_report import CostObservation

    today = datetime.now(UTC)
    obs = [
        CostObservation(start_time=today - timedelta(days=2), cost_usd=0.50),
        CostObservation(start_time=today - timedelta(days=1), cost_usd=0.25),
    ]

    with (
        patch.object(cost_report, "_build_langfuse_for_report", return_value=object()),
        patch.object(cost_report, "fetch_cost_observations", return_value=obs),
    ):
        rc = cost_report.main(["--days", "7", "--weekly"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Weekly Cost Summary" in out
    assert "week of " in out


def test_cli_returns_nonzero_when_langfuse_unconfigured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing credentials → graceful error, exit 1, no traceback."""
    from agentforge.observability import cost_report

    fake_settings = type(
        "_FakeSettings",
        (),
        {
            "langfuse_host": None,
            "langfuse_public_key": None,
            "langfuse_secret_key": None,
        },
    )()
    with patch.object(cost_report, "get_settings", return_value=fake_settings):
        rc = cost_report.main(["--days", "7"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Langfuse" in err


def test_project_production_spend_scales_avg_cost_per_turn_to_qps() -> None:
    """Task 27.4: given an average cost per turn and a projected QPS,
    return projected $/day, $/month under that load.

    Math: cost_per_turn × qps × seconds_per_day = $/day; ×30 = $/month.
    """
    from agentforge.observability.cost_report import project_production_spend

    proj = project_production_spend(
        avg_cost_per_turn=0.05,
        projected_qps=1.5,
    )
    # 0.05 * 1.5 * 86400 = 6480.
    assert proj.daily == pytest.approx(6480.0, rel=1e-9)
    # 30 day month.
    assert proj.monthly == pytest.approx(6480.0 * 30, rel=1e-9)


def test_project_production_spend_with_zero_qps_is_zero() -> None:
    from agentforge.observability.cost_report import project_production_spend

    proj = project_production_spend(avg_cost_per_turn=0.05, projected_qps=0.0)
    assert proj.daily == 0.0
    assert proj.monthly == 0.0


def test_project_production_spend_rejects_negative_inputs() -> None:
    from agentforge.observability.cost_report import project_production_spend

    with pytest.raises(ValueError):
        project_production_spend(avg_cost_per_turn=-0.01, projected_qps=1.0)
    with pytest.raises(ValueError):
        project_production_spend(avg_cost_per_turn=0.01, projected_qps=-1.0)


def test_average_cost_per_observation_handles_empty_input() -> None:
    """Empty observation list → 0.0 (no division-by-zero crash)."""
    from agentforge.observability.cost_report import average_cost_per_observation

    assert average_cost_per_observation([]) == 0.0


def test_average_cost_per_observation_returns_mean() -> None:
    from agentforge.observability.cost_report import (
        CostObservation,
        average_cost_per_observation,
    )

    obs = [
        CostObservation(start_time=datetime(2026, 5, 1, tzinfo=UTC), cost_usd=0.10),
        CostObservation(start_time=datetime(2026, 5, 1, tzinfo=UTC), cost_usd=0.20),
        CostObservation(start_time=datetime(2026, 5, 1, tzinfo=UTC), cost_usd=0.30),
    ]
    assert average_cost_per_observation(obs) == pytest.approx(0.20, rel=1e-9)


def test_cli_project_qps_flag_emits_projection_block(
    patched_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--project-qps 0.5`` adds a projection footer to the daily
    report driven by the observed average per-turn cost."""
    from agentforge.observability import cost_report
    from agentforge.observability.cost_report import CostObservation

    today = datetime.now(UTC)
    obs = [
        CostObservation(start_time=today - timedelta(hours=1), cost_usd=0.04),
        CostObservation(start_time=today - timedelta(hours=2), cost_usd=0.06),
    ]

    with (
        patch.object(cost_report, "_build_langfuse_for_report", return_value=object()),
        patch.object(cost_report, "fetch_cost_observations", return_value=obs),
    ):
        rc = cost_report.main(["--days", "1", "--project-qps", "0.5"])

    assert rc == 0
    out = capsys.readouterr().out
    # Average per turn = 0.05; at 0.5 QPS daily = 0.05 * 0.5 * 86400 = 2160.
    assert "Production Spend Projection" in out
    assert "QPS:           0.5" in out
    assert "$/day:         $2160.00" in out
    # Monthly = daily * 30 = 64800.
    assert "$/month:       $64800.00" in out


def test_cli_returns_nonzero_when_fetch_raises(
    patched_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A network error from Langfuse should produce a clean exit-1, not
    bubble a stack trace to the user.
    """
    from agentforge.observability import cost_report

    with (
        patch.object(cost_report, "_build_langfuse_for_report", return_value=object()),
        patch.object(
            cost_report,
            "fetch_cost_observations",
            side_effect=ConnectionError("boom"),
        ),
    ):
        rc = cost_report.main(["--days", "7"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Langfuse query failed" in err
    assert "ConnectionError" in err
