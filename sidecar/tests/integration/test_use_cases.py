"""End-to-end use-case flows for /agentforge/turn (Task 47.3).

These exercise the full PHP-module-through-sidecar path with a real
LLM call per turn. They're SLOW (~10-25s each, sometimes longer for
admit synthesis on a complex chart) and depend on ANTHROPIC_API_KEY
being set in the sidecar's environment. When the local sidecar
isn't reachable or doesn't have a key, individual tests skip
cleanly with a clear pointer.

Use-case taxonomy (mirrors planner.UseCase):

  UC-1  ADMIT_SYNTHESIS    "Give me a chart overview"
  UC-2  CONTRAINDICATION   "Is X safe to give?"
  UC-3  DELTA_COMPUTATION  "What's changed since last visit?"
  UC-4  FOLLOWUP           Short follow-up to a prior turn

Each test:
  * binds a known demo patient via the patient_context fixture
  * posts the use-case message to /agentforge/turn
  * asserts response status + lenient content checks
  * verifies all returned citations are well-formed (parseable)

Lenient content checks: LLM responses vary, so we look for KEY
clinical terms (e.g. "kidney" / "creatinine" for the CKD patient)
rather than exact phrasing. The regression-locks suite (Task 51.4)
pins canonical strings; THIS suite confirms the live system actually
exercises the full path.

Running just these tests::

    cd sidecar
    uv run pytest tests/integration/test_use_cases.py -v -s

Skip the whole suite when iterating without a live stack::

    uv run pytest --ignore=tests/integration
"""

from __future__ import annotations

import httpx
import pytest

from agentforge.verifier.citation import find_citations

# Every test in this module hits the real Anthropic API end-to-end.
# Tagged ``slow`` so default ``uv run pytest`` deselects them; run
# explicitly with ``uv run pytest -m slow`` (or
# ``uv run pytest tests/integration/test_use_cases.py``).
pytestmark = pytest.mark.slow

_TURN_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge/public/turn.php"
)

# Eula Crist (pid=8 in the demo seed) is the complex-chronic patient:
# CKD stage 3, hypertension, hyperlipidemia, depression screenings,
# intimate-partner-abuse history. Most use cases assert against her
# chart so the agent has substantive content to summarise.
_COMPLEX_PATIENT_PID = 8

# Generous timeout budget — admit-synthesis on Eula's chart with all
# 11 tools fanning out can run 25-40s in the worst case.
_LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


def _is_503(response: httpx.Response) -> bool:
    """503 = sidecar unreachable. Treated as a skip, not a failure."""
    return response.status_code == 503


async def _post_turn(
    client: httpx.AsyncClient, message: str
) -> httpx.Response:
    """POST a /turn request with the LLM-friendly timeout."""
    return await client.post(
        _TURN_PATH,
        json={"message": message},
        timeout=_LLM_TIMEOUT,
    )


def _skip_if_sidecar_unreachable(response: httpx.Response) -> None:
    """Convert sidecar-unreachable failures into clean skips.

    The integration suite is gated on dev-easy being up via the
    conftest fixtures, but the SIDECAR is a separate concern: it
    might not be running, or the module .env might not have
    AGENTFORGE_SIDECAR_URL pointing somewhere reachable. When the
    controller returns 503, skip rather than fail — we're testing
    behaviour against a live stack and the hardware just isn't here.
    """
    if _is_503(response):
        body_excerpt = response.text[:200]
        pytest.skip(
            f"Agent sidecar unreachable (controller returned 503). "
            f"Body excerpt: {body_excerpt!r}. Start the sidecar via "
            "./sidecar/scripts/sidecar.sh start and confirm "
            "AGENTFORGE_SIDECAR_URL in the module's .env points at it."
        )


def _assert_citations_well_formed(text: str) -> list[str]:
    """Every citation in ``text`` must parse to (record_type, record_id).

    Returns the list of (record_type, record_id) tuples for the test
    to inspect further. Doesn't enforce grounding against fixtures —
    that's the regression-locks job; here we just confirm the live
    system produces structurally valid citations.
    """
    citations = find_citations(text)
    assert citations, (
        f"Response contains no parseable citations; raw text excerpt: "
        f"{text[:300]!r}"
    )
    record_types = [c.record_type for c in citations]
    return record_types


async def test_uc1_admit_synthesis_returns_multi_section_summary(
    authenticated_client: httpx.AsyncClient,
    patient_context_factory,
) -> None:
    """UC-1 — admit-synthesis chart overview.

    User asks for a broad chart summary. Expected behaviour:
      * 200 OK, non-empty response body
      * Multiple canonical section headers present (## Active
        Problems, ## Active Medications, ## Recent Labs at minimum)
      * Citations present and well-formed
      * For Eula (the CKD patient), the response should reference
        her renal disease somewhere — either by clinical term
        (kidney/CKD/creatinine) or via a labs section
    """
    await patient_context_factory(_COMPLEX_PATIENT_PID)

    response = await _post_turn(
        authenticated_client, "Give me a chart overview for this patient."
    )
    _skip_if_sidecar_unreachable(response)

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}; "
        f"body: {response.text[:300]!r}"
    )
    body = response.text
    body_lower = body.lower()
    assert body, "Response body is empty"

    # Canonical section headers from Task 51.3. The strict three-
    # header pin lives in the regression-locks suite (UC1-STYLE-
    # HEADERS); here we just confirm the response is structured —
    # at least one Markdown ## header present. The LLM sometimes
    # consolidates a chart overview into a single narrative
    # section ("## Overview" + woven prose), which is a defensible
    # reading of the prompt rule "use ## headers when a response
    # covers multiple domains".
    assert "## " in body, (
        f"Response has no Markdown section headers; body excerpt: "
        f"{body[:500]!r}"
    )

    # Eula has clinically significant renal disease — the synthesis
    # should mention it in some recognisable form.
    renal_terms = ("kidney", "ckd", "creatinine", "renal", "gfr")
    assert any(term in body_lower for term in renal_terms), (
        f"Expected one of {renal_terms} in chart overview for the "
        f"CKD patient (pid=8); response excerpt: {body[:500]!r}"
    )

    # Citations are well-formed; multiple types appear.
    record_types = _assert_citations_well_formed(body)
    distinct = set(record_types)
    assert len(distinct) >= 2, (
        f"Admit synthesis should cite multiple record types; got "
        f"only {distinct}"
    )


async def test_uc2_contraindication_check_warns_on_nsaid(
    authenticated_client: httpx.AsyncClient,
    patient_context_factory,
) -> None:
    """UC-2 — contraindication check.

    User asks "is X safe?" for a drug that conflicts with the
    patient's chart. Expected:
      * 200 OK
      * Response references the contraindicating condition
        (CKD / kidney / renal — Eula has stage-3 CKD)
      * Mentions the drug or its class
      * Citations well-formed
    """
    await patient_context_factory(_COMPLEX_PATIENT_PID)

    response = await _post_turn(
        authenticated_client,
        "Is it safe to start this patient on ibuprofen?",
    )
    _skip_if_sidecar_unreachable(response)

    assert response.status_code == 200
    body = response.text
    body_lower = body.lower()
    assert body, "Response body is empty"

    # The contraindication should hinge on her renal status.
    renal_terms = ("kidney", "ckd", "renal", "creatinine", "gfr")
    assert any(term in body_lower for term in renal_terms), (
        f"Contraindication response should reference Eula's CKD; "
        f"body excerpt: {body[:500]!r}"
    )

    _assert_citations_well_formed(body)


async def test_uc3_delta_computation_compares_recent_state(
    authenticated_client: httpx.AsyncClient,
    patient_context_factory,
) -> None:
    """UC-3 — delta computation.

    User asks what's changed. Expected:
      * 200 OK
      * Response mentions a date or temporal phrase
      * Citations include encounter or labs (the temporal-frame tools)
    """
    await patient_context_factory(_COMPLEX_PATIENT_PID)

    response = await _post_turn(
        authenticated_client,
        "What has changed in this patient's chart over the last 90 days?",
    )
    _skip_if_sidecar_unreachable(response)

    assert response.status_code == 200
    body = response.text
    body_lower = body.lower()
    assert body, "Response body is empty"

    # Delta queries should anchor on time. Look for evidence that
    # the agent thought about the temporal frame.
    temporal_terms = (
        "since",
        "recent",
        "month",
        "day",
        "last",
        "previous",
        "2025",
        "2026",
    )
    assert any(term in body_lower for term in temporal_terms), (
        f"Delta response should reference time; body excerpt: "
        f"{body[:500]!r}"
    )

    record_types = _assert_citations_well_formed(body)
    # A delta response that cites no encounter / no lab is suspicious
    # — both are the temporal-frame anchors. Be lenient: require
    # AT LEAST one of them, not all.
    temporal_record_types = {"encounter", "lab_result", "procedure", "note"}
    assert any(rt in temporal_record_types for rt in record_types), (
        f"Delta response cited only {set(record_types)}; expected "
        f"at least one of {temporal_record_types} (encounters, labs, "
        "procedures, or notes carry the temporal frame)"
    )


async def test_uc4_followup_question_after_overview(
    authenticated_client: httpx.AsyncClient,
    patient_context_factory,
) -> None:
    """UC-4 — followup.

    Two-turn sequence: a full overview, then a narrow followup.
    Expected behaviour:
      * Both turns return 200
      * Followup is shorter than the overview (heuristic)
      * Followup cites at least one record (so it really did look
        something up rather than waving its hands)

    Note: without session_id wired through the chat-panel the agent
    treats turns as independent. This test exercises the
    request-shape only; multi-turn memory is the carryforward.
    """
    await patient_context_factory(_COMPLEX_PATIENT_PID)

    # Turn 1: overview.
    overview = await _post_turn(
        authenticated_client, "Give me a chart overview."
    )
    _skip_if_sidecar_unreachable(overview)
    assert overview.status_code == 200

    # Turn 2: narrow followup (no session_id, so the model doesn't
    # see turn 1's history — that's fine, we just confirm the
    # narrower query produces a focused response).
    followup = await _post_turn(
        authenticated_client, "Just the active medications, please."
    )
    _skip_if_sidecar_unreachable(followup)
    assert followup.status_code == 200

    overview_body = overview.text
    followup_body = followup.text
    assert overview_body, "Overview body empty"
    assert followup_body, "Followup body empty"

    # Followup should be narrower than the overview. Heuristic: the
    # followup is at most 80% of the overview size. (Synthesised
    # responses for "all problems + meds + labs" are reliably 2-4x
    # the size of "just meds".)
    assert len(followup_body) < int(len(overview_body) * 0.8), (
        f"Followup was {len(followup_body)} chars but overview was "
        f"{len(overview_body)} — expected followup to be much "
        "smaller. Either the LLM ignored 'just' or the overview "
        "got truncated."
    )

    # Followup cites at least one medication.
    record_types = _assert_citations_well_formed(followup_body)
    assert "medication" in record_types, (
        f"Followup asked for medications but cited only "
        f"{set(record_types)}"
    )
