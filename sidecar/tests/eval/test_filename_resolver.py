"""Tests for the FilenameDocumentResolver (production resolver impl).

The resolver parses document filenames out of an EvalCase query and
loads + renders the on-disk fixture into RenderedPage objects. It's the
bridge between case YAML (which references filenames in prose) and the
graph's PDF input shape.
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentforge.eval.filename_resolver import FilenameDocumentResolver
from agentforge.tools.attach_and_extract import RenderedPage
from tests.eval.harness import EvalCase, EvalCategory


def _case_with_query(query: str, *, case_id: str = "w2_test") -> EvalCase:
    return EvalCase(
        id=case_id,
        category=EvalCategory.EXTRACTION,
        patient_id=1,
        query=query,
        expected_behavior="...",
    )


class _FakePdfRenderer:
    """Drop-in for ``PdfRenderer`` that records calls + returns canned pages."""

    def __init__(self, pages: list[RenderedPage]) -> None:
        self._pages = pages
        self.calls: list[bytes] = []

    def render_pages(self, pdf_bytes: bytes) -> list[RenderedPage]:
        self.calls.append(pdf_bytes)
        return list(self._pages)


def _rendered_page() -> RenderedPage:
    return RenderedPage(
        page_number=1,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=850,
        pixel_height=1100,
    )


class TestFilenameParsing:
    def test_no_filename_in_query_returns_empty(self, tmp_path: pathlib.Path) -> None:
        renderer = _FakePdfRenderer([_rendered_page()])
        resolver = FilenameDocumentResolver(
            corpus_root=tmp_path,
            pdf_renderer=renderer,
        )

        pages, doc_id = resolver.resolve(
            _case_with_query("What is the A1C target for adults?")
        )

        assert pages == []
        assert doc_id is None
        assert renderer.calls == []

    def test_pdf_filename_resolves_to_pages(self, tmp_path: pathlib.Path) -> None:
        # On-disk fixture: a real-looking path with a stub byte payload.
        intake_dir = tmp_path / "intake-forms"
        intake_dir.mkdir()
        target = intake_dir / "p01-chen-intake-typed.pdf"
        target.write_bytes(b"%PDF-1.4 stub")

        renderer = _FakePdfRenderer([_rendered_page()])
        resolver = FilenameDocumentResolver(
            corpus_root=tmp_path,
            pdf_renderer=renderer,
        )

        pages, doc_id = resolver.resolve(
            _case_with_query(
                "Extract the intake form (p01-chen-intake-typed.pdf)."
            )
        )

        assert len(pages) == 1
        assert doc_id is not None
        # Renderer was called with the file's bytes.
        assert renderer.calls == [b"%PDF-1.4 stub"]

    def test_png_filename_skips_renderer_and_returns_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        # PDF renderer can't process PNGs; the resolver must skip rather
        # than crash. Returning empty pages is honest: extraction can't
        # run, the supervisor will route to evidence/synthesize and the
        # eval reflects that.
        intake_dir = tmp_path / "intake-forms"
        intake_dir.mkdir()
        target = intake_dir / "p03-reyes-intake.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n")

        renderer = _FakePdfRenderer([_rendered_page()])
        resolver = FilenameDocumentResolver(
            corpus_root=tmp_path,
            pdf_renderer=renderer,
        )

        pages, doc_id = resolver.resolve(
            _case_with_query("Extract from p03-reyes-intake.png.")
        )

        assert pages == []
        # Renderer was never called — PNG path skipped at the parser layer.
        assert renderer.calls == []
        # We still emit a synthetic doc_id so logs distinguish "PNG, can't
        # render" from "no document referenced at all."
        assert doc_id is not None

    def test_missing_file_does_not_crash(self, tmp_path: pathlib.Path) -> None:
        # Filename in the query but no on-disk fixture. The resolver
        # logs and returns empty rather than raising — the suite should
        # still run end-to-end, with that case's extraction pass empty.
        renderer = _FakePdfRenderer([_rendered_page()])
        resolver = FilenameDocumentResolver(
            corpus_root=tmp_path,
            pdf_renderer=renderer,
        )

        pages, doc_id = resolver.resolve(
            _case_with_query("Extract from p99-missing.pdf.")
        )

        assert pages == []
        assert doc_id is None  # Missing file → caller treats as "no doc"
