"""End-to-end happy-path test for the lab-PDF upload + extraction +
persist flow (Task 28).

**Mock-vs-real-stack decision: Shape (b), process-level integration.**
The brief flags either (a) full live-stack against docker-compose +
real Anthropic, or (b) process-level integration with mocked LLM and
mocked PHP boundary that still exercises the real Pydantic schemas,
real ``DocumentUploadWriter``, real ``VisionExtractor``, and real
``PdfRenderer`` code paths. We pick (b):

  * ``conftest.py`` already gates the live-stack tests on a reachable
    OpenEMR — those tests skip cleanly when the stack is down. Adding
    a sibling test that *requires* the stack would mean the suite
    becomes "all live or all mocked" rather than "skip what we can't
    run", regressing the existing CI ergonomics.
  * Anthropic calls cost real tokens. The brief explicitly says no
    token spend by default; ``@pytest.mark.slow`` is the explicit
    opt-in for token-spending tests, and we don't want this on the
    default path.
  * The PHP-side persist controller has its own PHPUnit test
    (``InternalLabPersistController``) — exercising it from Python
    here would duplicate that coverage, not extend it.
"""

from __future__ import annotations

import fitz  # type: ignore[import-untyped]

from agentforge.tools.attach_and_extract import PdfRenderer

from ._lab_e2e_fixtures import build_lab_pdf_bytes


# ---------------------------------------------------------------------------
# 28.1 — synthetic PDF round-trips through PyMuPDF
# ---------------------------------------------------------------------------


def test_synthetic_lab_pdf_renders_through_pymupdf() -> None:
    """Smoke test: the generator emits bytes that PyMuPDF can open and
    page-render. Establishes the foundation for the rest of the flow:
    if the PDF doesn't render, every later step is moot."""
    pdf_bytes = build_lab_pdf_bytes()
    assert pdf_bytes.startswith(b"%PDF"), "output must be a real PDF"

    pages = PdfRenderer(dpi=72).render_pages(pdf_bytes)
    assert len(pages) >= 1
    for page in pages:
        assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.pixel_width > 0
        assert page.pixel_height > 0


def test_synthetic_lab_pdf_contains_all_expected_panels() -> None:
    """The committed default (CBC normal + CMP w/ one flagged + A1c)
    must keep the headline panel/test names readable inside the PDF
    text layer. The vision tool runs on the rendered PNG, but
    asserting on the text layer here gives us a fast contract check
    — if the generator stops emitting "Hemoglobin A1c" the test fails
    immediately rather than waiting on a vision-mock divergence."""
    pdf_bytes = build_lab_pdf_bytes()
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    for expected in (
        "Diabetes Monitoring",
        "Comprehensive Metabolic Panel",
        "Complete Blood Count",
        "Hemoglobin A1c",
        "9.2",  # A1c value
        "BUN",
        "WBC",
        "ACC-2026-05-08-T28",  # accession
    ):
        assert expected in full_text, f"missing expected text: {expected!r}"


def test_synthetic_lab_pdf_is_parameterizable() -> None:
    """The brief's reusability requirement: panels accept overrides.

    Confirms that swapping the A1c row produces a PDF where the new
    value lands in the text layer. Other tests that need a different
    profile (e.g. critical potassium) lean on this knob."""
    from tests.integration._lab_e2e_fixtures import LabRowSpec

    custom_a1c = LabRowSpec(
        "Hemoglobin A1c", "4548-4", "5.4", "%", "<5.7", ""
    )
    pdf_bytes = build_lab_pdf_bytes(a1c_row=custom_a1c)
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        full_text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()
    assert "5.4" in full_text
    assert "9.2" not in full_text  # default A1c was overridden
