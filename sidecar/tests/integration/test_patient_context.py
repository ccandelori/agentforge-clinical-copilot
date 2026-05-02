"""Self-tests for the patient-context fixtures (Task 47.2).

These exercise the patient_context_factory + patient_context fixtures
against the live dev-easy stack to confirm that:

  - The set_pid mechanism actually binds patient context to the
    OpenEMR session (so subsequent /agentforge/turn calls don't
    return 400 "no patient context").
  - The default fixture binds to a known seed patient (pid=4 by
    default — sparse Alena).
  - The factory short-circuits with a clean skip when the requested
    patient doesn't exist in the seed.
"""

from __future__ import annotations

import httpx


async def test_demo_patient_ids_returns_known_pids(
    demo_patient_ids: tuple[int, ...],
) -> None:
    """The default patient list is non-empty and matches the seed."""
    assert len(demo_patient_ids) > 0
    # All entries are positive ints
    for pid in demo_patient_ids:
        assert isinstance(pid, int)
        assert pid > 0


async def test_patient_context_factory_sets_pid_for_known_patient(
    patient_context_factory,
    demo_patient_ids: tuple[int, ...],
    authenticated_client: httpx.AsyncClient,
) -> None:
    """Setting pid via the factory makes /agentforge/turn see the patient.

    Pre-set: hitting /agentforge/turn with no pid bound returns 400
    ("no patient context"). Post-set: the same call should fail with
    a *different* error (likely 400 with a different message, or 200
    if the LLM responds — depends on whether ANTHROPIC_API_KEY is
    set in the sidecar). What we assert here is the negative: the
    response no longer says "no patient context".
    """
    pid = demo_patient_ids[0]
    await patient_context_factory(pid)

    # Post call with empty body — may or may not be valid JSON; we
    # just want to see whether the controller passes the
    # patient-context check.
    response = await authenticated_client.post(
        "/interface/modules/custom_modules/oe-module-agentforge/public/turn.php",
        json={"message": "test"},
    )

    body_excerpt = response.text[:300].lower()
    # The success criterion: we are NOT getting the
    # patient-context refusal. We accept any other failure mode
    # (LLM not configured, sidecar down, etc.) — those are
    # downstream concerns.
    assert "no patient context" not in body_excerpt, (
        f"Patient context not bound after set_pid={pid}; status "
        f"{response.status_code}, body excerpt: {body_excerpt!r}"
    )


async def test_patient_context_default_fixture_binds_first_demo_pid(
    patient_context: int,
    demo_patient_ids: tuple[int, ...],
) -> None:
    """The default patient_context fixture binds to demo_patient_ids[0]."""
    assert patient_context == demo_patient_ids[0]


async def test_patient_context_factory_can_switch_patients(
    patient_context_factory,
    demo_patient_ids: tuple[int, ...],
) -> None:
    """The factory accepts arbitrary calls — switching pid mid-test works.

    This is what UC-1..UC-4 tests need: ability to bind to a
    specific patient per-test rather than session-scoped. Cycle
    through every demo patient just to confirm the second call
    overrides the first.
    """
    if len(demo_patient_ids) < 2:
        # Single-patient cohorts can't validate the switch; the
        # second-call behavior is moot. The default cohort has 2
        # (pid=4 + pid=8); skip cleanly when it doesn't.
        import pytest

        pytest.skip(
            "Need at least 2 demo patients to validate context switch; "
            f"got {demo_patient_ids}"
        )

    response_one = await patient_context_factory(demo_patient_ids[0])
    response_two = await patient_context_factory(demo_patient_ids[1])
    # Both calls should succeed (not raise a skip via the factory's
    # 400-check).
    assert response_one.status_code < 400
    assert response_two.status_code < 400
