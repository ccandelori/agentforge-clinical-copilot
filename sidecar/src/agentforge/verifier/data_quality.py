"""Inline data-quality flags for synthesized agent answers.

Two heuristics live here, both deliberately simple. The MVP goal is to
let the user see *next to a citation* when the data underlying it is
either out-of-date (stale lab) or contradicted by a more recent source
(problem list vs. note). A future pass can route the same checks
through an LLM-as-judge — that path is explicitly deferred per
ARCHITECTURE.md S6 — but the structural flags here are cheap enough to
run on every turn.

Design notes
------------

* **Clock injection.** The class takes a ``now: Callable[[], datetime]``
  in the constructor. Tests pass a frozen closure; production wires in
  ``lambda: datetime.now(UTC)``. We never call ``datetime.now()`` from
  business logic, per CLAUDE.md.
* **Date arithmetic, not strings.** ``check_stale_labs`` works in
  ``date`` space (no formatted-string parsing). ``check_conflicting_sources``
  walks free-text bodies, but pure substring + cue matching — no date
  manipulation needed.
* **No standalone integration.** This class is consumed by the
  orchestrator/streaming verifier in a separate task; here we ship a
  tested, importable unit.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import final

from agentforge.tools.labs import LabResultItem
from agentforge.tools.notes import NoteItem
from agentforge.tools.problems import ProblemItem

# Negation cues that, when they appear in a note body alongside a
# problem-list entry's title, mean "this condition is no longer
# active". Kept short on purpose — false positives here would bury the
# user in noise, and the tail of obscure phrasings is the kind of
# coverage gap an LLM-as-judge pass is supposed to fill later.
_NEGATION_CUES: tuple[str, ...] = (
    "resolved",
    "discontinued",
    "no longer",
    "cleared",
    "off",
)

# Sentence-ish splitter. Clinical notes don't follow strict prose
# conventions, so we split on sentence terminators *and* line breaks /
# semicolons / em-dashes — anything that meaningfully ends a clause.
# An MVP-grade tokenizer; if we ever need real chunking the right answer
# is a proper NLP library, not more regex.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]|—| - ")


@final
@dataclass(frozen=True, slots=True)
class DataQualityChecker:
    """Adds inline data-quality flags to verifier output.

    Construct once per turn (or once at startup — it's stateless except
    for the injected clock) and call the check methods with the
    relevant typed records. Flags are returned as plain strings; the
    caller decides where to splice them into the synthesized answer.
    """

    now: Callable[[], datetime]
    stale_lab_threshold_days: int = 30

    def check_stale_labs(
        self,
        lab_result: LabResultItem,
        expected_recency_days: int | None = None,
    ) -> str | None:
        """Return an inline flag if ``lab_result`` is older than the threshold.

        ``expected_recency_days`` overrides ``stale_lab_threshold_days``
        for callers that know the analyte deserves a tighter window
        (e.g. a glucose question expects newer values than a yearly
        cholesterol panel).

        Returns ``None`` for fresh labs and for labs with no recorded
        date — we can't flag what we can't measure, and absence is
        already visible to the model via the missing field.
        """
        if lab_result.date is None:
            return None

        threshold = (
            expected_recency_days
            if expected_recency_days is not None
            else self.stale_lab_threshold_days
        )

        # Compare date-to-date so DST and tz quirks don't shift the
        # boundary by a day.
        today = self.now().date()
        age_days = (today - lab_result.date).days
        if age_days <= threshold:
            return None

        return (
            f"(data from {lab_result.date.isoformat()} — "
            "confirm if newer values expected)"
        )

    def check_conflicting_sources(
        self,
        problems: Iterable[ProblemItem],
        notes: Iterable[NoteItem],
    ) -> list[str]:
        """Return one description per detected problem/note conflict.

        MVP heuristic: for each active problem, walk every note body
        chunk (sentence-ish; see :data:`_SENTENCE_SPLIT_RE`) and look
        case-insensitively for a chunk that contains both the problem
        title *and* one of :data:`_NEGATION_CUES`. The same-chunk
        constraint is what stops "Hypertension resolved. Diabetes is
        active." from flagging Diabetes as a conflict.

        Returns an empty list when no conflicts are detected.
        """
        # Materialize once: we iterate problems for every note and vice
        # versa, so we cannot rely on single-shot iterables.
        problem_list = list(problems)
        if not problem_list:
            return []

        note_list = [n for n in notes if n.body]
        if not note_list:
            return []

        conflicts: list[str] = []
        for problem in problem_list:
            title = problem.title.strip()
            if not title:
                continue
            title_lower = title.lower()

            for note in note_list:
                # _Conflict_ requires both: the title appears AND a
                # negation cue appears in the *same sentence-ish chunk*
                # of the note body. Body is non-empty by the filter
                # above, but mypy still wants the narrowing.
                body = note.body or ""
                cue = _cue_in_same_chunk(body, title_lower)
                if cue is None:
                    continue
                conflicts.append(_format_conflict(problem, note, cue))

        return conflicts


def _cue_in_same_chunk(body: str, title_lower: str) -> str | None:
    """Return the first negation cue that shares a chunk with the title.

    We split the note body into sentence-ish chunks (terminator-based
    split, lower-cased) and look for a chunk containing both the
    problem title and a cue. This bounds the heuristic to a clause —
    "Hypertension resolved" stays attached to Hypertension and not to
    a different condition mentioned in the next sentence.
    """
    for raw_chunk in _SENTENCE_SPLIT_RE.split(body):
        chunk_lower = raw_chunk.lower()
        if title_lower not in chunk_lower:
            continue
        for cue in _NEGATION_CUES:
            if cue in chunk_lower:
                return cue
    return None


def _format_conflict(problem: ProblemItem, note: NoteItem, cue: str) -> str:
    """Render a human-readable conflict description.

    The shape is intentionally compact — this string lands inline in
    the final answer, so we lead with the conflicting condition and
    surface the date so the clinician can find the source quickly.
    """
    note_date = note.date or "an unknown date"
    return (
        f"Problem list shows '{problem.title}' as active, but a note from "
        f"{note_date} describes it as {cue}. Confirm current status."
    )
