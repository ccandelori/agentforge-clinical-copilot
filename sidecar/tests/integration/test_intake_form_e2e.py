"""End-to-end intake-form upload + extraction test (Task 29).

This test exercises the full intake-form flow at the *process boundary*:

    PDF bytes → BFF /api/agent/upload → extraction worker → persist

It runs offline. The Anthropic vision client is replaced with an
``AsyncMock`` returning a canned :class:`IntakeFormExtraction` payload.
The PHP-side endpoints (``upload_document.php``,
``persist_questionnaire_response.php``) are stubbed with
``httpx.MockTransport`` handlers that record every URL the sidecar
hits. No real DB, no real Anthropic, no dev-easy stack.

Why a process-level test (and not a real-stack one): the load-bearing
W2 architectural invariant is that intake forms write to
``QuestionnaireResponse`` and *never* to clinical tables
(``patient_data``, ``medications``, ``allergies``, ``family_history``).
That invariant manifests as a routing constraint at the sidecar/PHP
boundary — a regression would show up as the sidecar POSTing to a
clinical-table endpoint. By stubbing PHP with MockTransport we get a
strict allowlist of "URLs the e2e flow may hit" — the test fails
loudly if the route ever expands to a clinical-write endpoint, which
is exactly the case the architecture invariant forbids.

The test does NOT depend on the integration suite's ``conftest.py``
fixtures (the live-OpenEMR ones); pytest only invokes those when a
test asks for them by name. We declare none, so the test file is
self-contained even though it shares the directory.

The companion lab e2e (Task 28) lives in a parallel worktree and
does NOT share this file's fixtures via a conftest — see
:mod:`_intake_e2e_fixtures` for the rationale.

This commit lands subtask 29.1 (the PDF generator + smoke tests).
Subsequent commits add the upload, extraction, persistence, and
audit-ordering phases against the same fixture surface.
"""

from __future__ import annotations

import fitz  # type: ignore[import-untyped]

from tests.integration._intake_e2e_fixtures import (
    PINNED_INTAKE_CONTENT,
    build_intake_pdf_bytes,
)

# ---------------------------------------------------------------------------
# Subtask 29.1 — PDF generator smoke test
# ---------------------------------------------------------------------------


def test_intake_pdf_generator_produces_parseable_pdf_with_all_five_sections() -> None:
    """The synthetic intake PDF parses through PyMuPDF and contains
    the five sections Task 29 requires (demographics, chief concern,
    medications, allergies, family history).

    This is the headline acceptance gate for subtask 29.1: if the
    generator ever drops a section or stops emitting valid PDF bytes,
    the e2e flow can't even start.
    """
    pdf_bytes = build_intake_pdf_bytes()

    # Header check — every PDF starts with %PDF-.
    assert pdf_bytes.startswith(b"%PDF-")
    # Generator output should be small but non-trivial. Empty would
    # mean the generator silently dropped its body.
    assert len(pdf_bytes) > 1000

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    # All five Task-29 sections must be present in the rendered form.
    for section in (
        "Patient Information",        # demographics
        "Chief Concern",              # chief concern
        "Current Medications",        # medications
        "Allergies",                  # allergies
        "Family Medical History",     # family history
    ):
        assert section in full_text, f"PDF missing section: {section!r}"

    # And the pinned content must round-trip through the renderer so
    # the canned-extraction values match what the source actually says.
    assert PINNED_INTAKE_CONTENT.chief_concern in full_text
    assert PINNED_INTAKE_CONTENT.medications[0][0] in full_text  # "Metformin"
    assert PINNED_INTAKE_CONTENT.allergies[0][0] in full_text     # "Penicillin"


def test_intake_pdf_generator_is_deterministic() -> None:
    """Two builds with the same content emit byte-identical PDFs.

    ReportLab's ``invariant=1`` flag strips the per-run trailer ID; if
    a future ReportLab upgrade silently changes that behavior, the
    e2e test would start carrying timestamp-shaped flakiness. Pin it
    here so the regression is obvious."""
    a = build_intake_pdf_bytes()
    b = build_intake_pdf_bytes()
    assert a == b
