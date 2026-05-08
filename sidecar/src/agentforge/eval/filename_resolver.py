"""Filename-based DocumentFixtureResolver.

The W2 eval cases reference document filenames in their query text
(e.g. ``"Extract the intake form (p01-chen-intake-typed.pdf)."``). This
resolver:

  1. Parses the first ``p\\d{2}-\\w+\\.(pdf|png)`` token out of the query.
  2. Resolves it against ``corpus_root`` (typically
     ``week2/example-documents/`` at the repo root).
  3. For PDFs: reads bytes, runs them through ``PdfRenderer.render_pages``,
     and returns the rendered pages plus a synthetic document_id.
  4. For PNGs: returns empty pages — ``PdfRenderer`` doesn't accept PNGs,
     and the W2 graph's intake_extractor_node short-circuits when pages
     are empty, which is the honest behaviour for "can't render this file
     type as a PDF."

The synthetic document_id is the case-id + filename hash collapsed to an
int — stable across runs, never collides with real OpenEMR document
IDs (it's local to the eval pipeline).

Design note on PNG support
--------------------------

The production /turn route also rejects PNGs at the renderer (see
``main.turn`` — 422 if mimetype is not application/pdf). The resolver
mirrors that: PNG cases land empty-pages, the supervisor routes around
extraction, and the eval reflects "agent answered without extracting
the PNG." If a future change adds PNG → page rendering, this resolver
is the one place to update.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import re
from collections.abc import Iterable
from typing import Final, Protocol

from agentforge.tools.attach_and_extract import RenderedPage
from tests.eval.harness import EvalCase

logger = logging.getLogger(__name__)


class _PdfRendererLike(Protocol):
    """Minimal Protocol the resolver consumes.

    Real ``agentforge.tools.attach_and_extract.PdfRenderer`` satisfies
    this structurally. Tests pass a fake.
    """

    def render_pages(self, pdf_bytes: bytes) -> list[RenderedPage]: ...


# Filename pattern: pNN-<name>.<ext>. Anchored on word boundary so
# parenthesised mentions (``"(p01-chen-intake-typed.pdf)"``) match.
_FILENAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(p\d{2}-[a-z0-9_-]+\.(?:pdf|png))\b",
    re.IGNORECASE,
)


# Subdirectories the resolver checks under ``corpus_root``. Search is
# in this order so an unambiguously-named intake form wins over a same-
# stem file in another folder.
_SEARCH_SUBDIRS: Final[tuple[str, ...]] = ("intake-forms", "lab-results")


class FilenameDocumentResolver:
    """Resolves an EvalCase's query → (pages, document_id) by filename.

    Stateless beyond its constructor args — safe to share across the
    full suite. Construction is cheap; no I/O or model loads.
    """

    def __init__(
        self,
        *,
        corpus_root: pathlib.Path,
        pdf_renderer: _PdfRendererLike,
        search_subdirs: Iterable[str] = _SEARCH_SUBDIRS,
    ) -> None:
        self._corpus_root = corpus_root
        self._renderer = pdf_renderer
        self._search_subdirs = tuple(search_subdirs)

    def resolve(
        self, case: EvalCase
    ) -> tuple[list[RenderedPage], int | None]:
        """Return (pages, document_id) for ``case``.

        Returns ``([], None)`` when no filename appears in the query —
        the caller treats this as "evidence-only case." Returns ``([],
        None)`` when a filename appears but the file is missing on disk
        (logged at WARNING, suite continues). Returns ``([], synthetic_id)``
        when the file exists but is a PNG — the doc_id signals "case
        had a document reference" so logs can distinguish that from
        "no document mentioned at all."
        """
        match = _FILENAME_PATTERN.search(case.query)
        if match is None:
            return ([], None)

        filename = match.group(1).lower()
        path = self._find_in_corpus(filename)
        if path is None:
            logger.warning(
                "case %s references %s but file not found under %s",
                case.id,
                filename,
                self._corpus_root,
            )
            return ([], None)

        synthetic_id = _synthetic_document_id(filename)

        if path.suffix.lower() != ".pdf":
            # PNG (or any non-PDF). The PdfRenderer can't handle it.
            # Return the synthetic id so callers know the case mentioned
            # a document; pages stay empty so the extractor short-circuits.
            return ([], synthetic_id)

        try:
            pdf_bytes = path.read_bytes()
            pages = self._renderer.render_pages(pdf_bytes)
        except (OSError, ValueError):
            logger.warning(
                "case %s: failed to render %s — skipping extraction",
                case.id,
                path,
                exc_info=True,
            )
            return ([], None)

        return (pages, synthetic_id)

    def _find_in_corpus(self, filename: str) -> pathlib.Path | None:
        """Walk the configured search subdirs for ``filename``.

        First match wins. Returns None when the file is missing across
        every subdir.
        """
        for subdir in self._search_subdirs:
            candidate = self._corpus_root / subdir / filename
            if candidate.is_file():
                return candidate
        # Fallback: also check the root itself, for callers that pass a
        # flat directory of fixtures.
        root_candidate = self._corpus_root / filename
        if root_candidate.is_file():
            return root_candidate
        return None


def _synthetic_document_id(filename: str) -> int:
    """Map a filename to a stable 31-bit integer document id.

    Stable across runs (sha256 of the filename, truncated). The 31-bit
    range avoids collisions with real OpenEMR document_ids in the dev
    instance (those start at 1 and grow up; the synthetic ids are
    always >= 2^28, which is well above any realistic real id).
    """
    digest = hashlib.sha256(filename.encode("utf-8")).digest()
    # 4 bytes → uint32; clamp to positive 31-bit so the value fits in a
    # standard ``int`` field on Pydantic models without surprise.
    raw = int.from_bytes(digest[:4], byteorder="big")
    return (raw & 0x7FFFFFFF) | 0x10000000


__all__ = ("FilenameDocumentResolver",)
