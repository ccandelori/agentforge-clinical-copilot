"""Pytest entrypoint for the baseline eval suite.

Runs each :data:`BASELINE_CASES` case through the live
``/agentforge/turn`` endpoint and grades with the deterministic grader.
Tagged ``eval`` so the default ``uv run pytest`` skips the suite —
it spends real Anthropic tokens and takes 1-3 minutes per pass.

Run on demand::

    cd sidecar
    uv run pytest -m eval -v               # one line per case
    uv run pytest -m eval -v -s            # also print grader detail
    uv run pytest -m eval -k UC1           # narrow to one case

503s from the controller (sidecar unreachable) skip the whole case
the way the existing UC-flow tests do — the suite tests behavior
against a healthy stack, not infrastructure availability.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final
from uuid import uuid4

import httpx
import pytest

from tests.eval.baseline.cases import ALL_CASES, BaselineCase
from tests.eval.baseline.grader import grade

# Alias for the patient_context_factory fixture's return type. The
# fixture in tests/integration/conftest.py is annotated implicitly;
# pinning the shape here lets mypy validate the callsite without
# touching the upstream conftest.
PatientContextFactory = Callable[[int], Awaitable[httpx.Response]]

# 47.5 / 47.3 set this same path; reuse so the suite tracks any future
# rename in one place.
_TURN_PATH: Final[str] = (
    "/interface/modules/custom_modules/oe-module-agentforge/public/turn.php"
)

# 60s read timeout — synthesis on Eula's chart can hit ~25s. Add
# headroom for cold-cache or first-call slowdown.
_LLM_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=5.0, read=60.0, write=10.0, pool=5.0
)

# Module-level marker tags every test in this file. Default pytest
# config deselects ``eval`` so this only runs when explicitly asked.
pytestmark = pytest.mark.eval


@pytest.mark.parametrize("case", ALL_CASES, ids=[c.id for c in ALL_CASES])
async def test_baseline_case(
    case: BaselineCase,
    authenticated_client: httpx.AsyncClient,
    patient_context_factory: PatientContextFactory,
) -> None:
    """Run one baseline case end-to-end and assert grader passed.

    On stack-down (503), pytest.skip rather than fail — the suite
    exists to detect agent-behavior regressions, not to act as a
    stack uptime monitor. On any other status the grader runs and
    reports per-rule failures.
    """
    await patient_context_factory(case.patient_id)

    # Each case posts with a fresh session_id so multi-turn memory
    # state from one case doesn't leak into the next. Order
    # independence is a non-negotiable property of an eval suite.
    session_id = uuid4().hex

    response = await authenticated_client.post(
        _TURN_PATH,
        json={"message": case.query, "session_id": session_id},
        timeout=_LLM_TIMEOUT,
    )

    if response.status_code == 503:
        pytest.skip(
            f"Sidecar unreachable for case {case.id}: 503 from "
            f"controller. Body excerpt: {response.text[:160]!r}. "
            "Start ./sidecar/scripts/sidecar.sh and ensure "
            "AGENTFORGE_SIDECAR_URL points at it."
        )

    result = grade(case, response.text, response.status_code)

    # Always print one summary line per case so a failing run can be
    # diagnosed without re-running with -v. The detail_lines block
    # only renders on actual failure, keeping passing runs quiet.
    print(f"\n{result.summary_line()}")
    if not result.passed or result.warnings:
        for line in result.detail_lines():
            print(line)

    assert result.passed, (
        f"baseline case {case.id} failed: {list(result.failures)} "
        f"(case description: {case.description})"
    )
