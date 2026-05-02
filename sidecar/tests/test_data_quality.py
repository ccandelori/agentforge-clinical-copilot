"""DataQualityChecker — inline data-quality flags for synthesized answers.

Two checks live here:

* ``check_stale_labs`` — flags lab results whose collection date is older
  than ``STALE_LAB_THRESHOLD_DAYS`` (default 30) so the user can see the
  freshness caveat inline next to the cited value.
* ``check_conflicting_sources`` — flags the obvious case where the
  problem list still claims a condition is active but a recent note says
  it was resolved/discontinued/cleared. The MVP heuristic is a
  case-insensitive substring match plus a small set of negation cues.
  LLM-as-judge is explicitly deferred (see ARCHITECTURE.md).

The clock is injected as a ``Callable[[], datetime]`` so every test can
pin "now" and assert the boundary deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from agentforge.tools.labs import LabResultItem
from agentforge.tools.notes import NoteItem
from agentforge.tools.problems import ProblemItem
from agentforge.verifier.data_quality import DataQualityChecker


def _frozen_now(at: datetime) -> Callable[[], datetime]:
    """Return a closure that always emits ``at``.

    The production class types its clock as ``Callable[[], datetime]``;
    tests just need any deterministic value for "now".
    """

    def _now() -> datetime:
        return at

    return _now


_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _checker() -> DataQualityChecker:
    return DataQualityChecker(now=_frozen_now(_NOW))


def _lab(test_date: date | None) -> LabResultItem:
    return LabResultItem(
        id=1,
        order_id=10,
        report_id=100,
        test_code="A1C",
        test_name="Hemoglobin A1c",
        value="6.4",
        units="%",
        reference_range="4.0-5.6",
        abnormal="high",
        date=test_date,
    )


class TestCheckStaleLabs:
    def test_returns_none_for_fresh_lab(self) -> None:
        # 5 days old — well within the 30-day threshold.
        lab = _lab(date(2026, 4, 26))
        assert _checker().check_stale_labs(lab) is None

    def test_returns_none_when_lab_date_is_none(self) -> None:
        lab = _lab(None)
        assert _checker().check_stale_labs(lab) is None

    def test_returns_none_at_exact_threshold_boundary(self) -> None:
        # Exactly 30 days old: the threshold is exclusive ("older than"),
        # so 30 days should NOT be flagged. 31 days is the first stale day.
        lab = _lab(date(2026, 4, 1))  # 30 days before _NOW (2026-05-01).
        assert _checker().check_stale_labs(lab) is None

    def test_flags_lab_one_day_past_threshold(self) -> None:
        # 31 days old — first day past threshold.
        lab = _lab(date(2026, 3, 31))
        flag = _checker().check_stale_labs(lab)
        assert flag is not None
        assert "2026-03-31" in flag
        assert "confirm if newer values expected" in flag

    def test_flags_lab_far_past_threshold(self) -> None:
        lab = _lab(date(2025, 8, 15))  # ~8.5 months old
        flag = _checker().check_stale_labs(lab)
        assert flag == (
            "(data from 2025-08-15 — confirm if newer values expected)"
        )

    def test_respects_custom_recency_threshold(self) -> None:
        # With expected_recency_days=7, anything older than 7 days flags.
        lab = _lab(date(2026, 4, 20))  # 11 days old.
        checker = _checker()
        assert checker.check_stale_labs(lab, expected_recency_days=7) is not None
        # Default (30) wouldn't flag this.
        assert checker.check_stale_labs(lab) is None


class TestCheckConflictingSources:
    @staticmethod
    def _problem(title: str, problem_id: int = 1) -> ProblemItem:
        return ProblemItem(id=problem_id, title=title)

    @staticmethod
    def _note(body: str, note_id: int = 1, note_date: str = "2026-04-30") -> NoteItem:
        return NoteItem(
            id=note_id,
            source="pnote",
            date=note_date,
            author="Dr Smith",
            title="Follow-up",
            body=body,
            note_type="progress",
        )

    def test_no_conflicts_when_problems_empty(self) -> None:
        notes = [self._note("Patient reports hypertension cleared.")]
        assert _checker().check_conflicting_sources([], notes) == []

    def test_no_conflicts_when_notes_empty(self) -> None:
        problems = [self._problem("Hypertension")]
        assert _checker().check_conflicting_sources(problems, []) == []

    def test_no_conflict_when_problem_mentioned_without_negation(self) -> None:
        problems = [self._problem("Hypertension")]
        notes = [self._note("Continuing hypertension management. BP today 138/86.")]
        assert _checker().check_conflicting_sources(problems, notes) == []

    def test_flags_conflict_when_problem_marked_resolved_in_note(self) -> None:
        problems = [self._problem("Hypertension")]
        notes = [
            self._note(
                "Hypertension resolved after lifestyle changes; off lisinopril.",
                note_id=42,
            )
        ]
        conflicts = _checker().check_conflicting_sources(problems, notes)
        assert len(conflicts) == 1
        assert "Hypertension" in conflicts[0]
        assert "resolved" in conflicts[0].lower()

    def test_matching_is_case_insensitive(self) -> None:
        problems = [self._problem("Asthma")]
        notes = [self._note("ASTHMA NO LONGER an active issue.")]
        conflicts = _checker().check_conflicting_sources(problems, notes)
        assert len(conflicts) == 1
        assert "Asthma" in conflicts[0]

    def test_detects_multiple_negation_cues(self) -> None:
        problems = [
            self._problem("Hypertension", problem_id=1),
            self._problem("Asthma", problem_id=2),
            self._problem("Diabetes", problem_id=3),
        ]
        notes = [
            self._note(
                "Hypertension resolved. Asthma cleared per pulmonology. "
                "Diabetes well controlled — continuing metformin.",
                note_id=99,
            ),
        ]
        conflicts = _checker().check_conflicting_sources(problems, notes)
        # Hypertension + Asthma should flag; Diabetes should not.
        assert len(conflicts) == 2
        joined = "\n".join(conflicts)
        assert "Hypertension" in joined
        assert "Asthma" in joined
        assert "Diabetes" not in joined

    def test_skips_notes_with_empty_body(self) -> None:
        problems = [self._problem("Hypertension")]
        notes = [
            NoteItem(
                id=7,
                source="pnote",
                date="2026-04-30",
                body=None,
                permission_denied=True,
            ),
        ]
        assert _checker().check_conflicting_sources(problems, notes) == []
