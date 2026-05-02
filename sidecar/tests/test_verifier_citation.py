"""Citation parser — extracts ``[record_type #id, extra]`` tokens.

The parser is the verifier's first line of defense: it normalizes the
free-text citation tokens the model emits into structured Citation
records that the cache lookup and domain checks can act on. See
ARCHITECTURE.md S6.
"""

from __future__ import annotations

import pytest

from agentforge.verifier.citation import (
    Citation,
    find_citations,
    parse_citation,
)


class TestParseCitation:
    def test_parses_canonical_form(self) -> None:
        result = parse_citation("[encounter #38241, 2026-04-12]")
        assert result == Citation(
            record_type="encounter",
            record_id="38241",
            extra="2026-04-12",
            raw="[encounter #38241, 2026-04-12]",
        )

    def test_parses_form_without_extra(self) -> None:
        result = parse_citation("[problem #42]")
        assert result is not None
        assert result.record_type == "problem"
        assert result.record_id == "42"
        assert result.extra is None

    def test_parses_form_with_textual_extra(self) -> None:
        result = parse_citation("[medication #7, started 2024-08-15]")
        assert result is not None
        assert result.record_type == "medication"
        assert result.record_id == "7"
        assert result.extra == "started 2024-08-15"

    def test_returns_none_for_text_without_brackets(self) -> None:
        assert parse_citation("plain text") is None

    def test_returns_none_for_label_form_without_id(self) -> None:
        # [Rx: lisinopril 20mg, ...] is a label-form citation. MVP cache
        # lookup is ID-anchored, so a label-only citation can't be
        # validated against the per-turn cache. Future label resolution
        # would land alongside Task 29 domain constraints.
        assert parse_citation("[Rx: lisinopril 20mg, started 2024-08-15]") is None

    def test_record_type_must_start_with_letter(self) -> None:
        # Tightens against ID-only artefacts the model might emit.
        assert parse_citation("[#42]") is None
        assert parse_citation("[123 #42]") is None

    def test_record_id_allows_alphanumeric_and_dashes(self) -> None:
        # Some sources use UUID-shaped or hyphenated IDs.
        result = parse_citation("[lab_result #abc-123-xyz]")
        assert result is not None
        assert result.record_type == "lab_result"
        assert result.record_id == "abc-123-xyz"


class TestFindCitations:
    def test_returns_empty_list_when_no_citations(self) -> None:
        assert find_citations("Patient is doing well.") == []

    def test_returns_one_citation(self) -> None:
        text = "Per the chart [encounter #38241, 2026-04-12]."
        result = find_citations(text)
        assert len(result) == 1
        assert result[0].record_id == "38241"

    def test_returns_multiple_citations_in_order(self) -> None:
        text = (
            "First sentence [encounter #1, 2026-04-12]. "
            "Second sentence [problem #5]."
        )
        result = find_citations(text)
        assert [c.record_id for c in result] == ["1", "5"]
        assert [c.record_type for c in result] == ["encounter", "problem"]

    def test_skips_label_form_silently(self) -> None:
        # Label-form citations don't crash the parser; they're just not
        # recognized for ID-based validation.
        text = "Patient on [Rx: lisinopril 20mg]. Per [encounter #1, 2026-04-12]."
        result = find_citations(text)
        assert len(result) == 1
        assert result[0].record_id == "1"


class TestMultiIdCitations:
    """Multi-id citation forms get expanded into one Citation per id.

    The model occasionally emits compact references like
    ``[problem #293, #294]`` to credit two related rows for one
    factual claim. Without expansion the parser captures #293
    and stuffs ``#294`` into the ``extra`` field where the verifier
    cache never checks it — which means the second id could be
    fabricated and slip through grounding. Expansion here means
    every id in a multi-id form has to ground individually.
    """

    def test_two_ids_yield_two_citations_same_type(self) -> None:
        result = find_citations("Two related [problem #293, #294].")
        assert len(result) == 2
        assert [c.record_type for c in result] == ["problem", "problem"]
        assert [c.record_id for c in result] == ["293", "294"]

    def test_three_ids_yield_three_citations(self) -> None:
        result = find_citations("Triple [medication #21, #22, #23].")
        assert [c.record_id for c in result] == ["21", "22", "23"]
        assert all(c.record_type == "medication" for c in result)

    def test_textual_extra_not_treated_as_id(self) -> None:
        # `started 2024-08-15` is a date / phrase, not an id. We
        # should still parse this as ONE citation with the date in
        # extra — same behavior as before the multi-id support.
        result = find_citations("[medication #7, started 2024-08-15]")
        assert len(result) == 1
        assert result[0].record_id == "7"
        assert result[0].extra == "started 2024-08-15"

    def test_mixed_extra_with_id_and_text_falls_back_to_one_citation(
        self,
    ) -> None:
        # A mixed extra (id intermingled with non-id text) is too
        # ambiguous to expand cleanly. Conservative path: leave the
        # whole extra intact, emit one Citation, let the verifier
        # decide whether to ground or reject. Documents the
        # boundary so a future "fancier expansion" is an explicit
        # change rather than silent drift.
        result = find_citations("[problem #293, started 2024-01-01, #294]")
        assert len(result) == 1
        assert result[0].record_id == "293"

    def test_expanded_citations_share_the_raw_token(self) -> None:
        # Both Citation objects record the same raw token so
        # downstream code that compares back to the original text
        # (e.g. for redaction) sees a consistent source.
        result = find_citations("[problem #293, #294]")
        assert len(result) == 2
        assert result[0].raw == result[1].raw == "[problem #293, #294]"

    def test_expanded_secondary_id_has_no_extra(self) -> None:
        # The first citation still holds the original parse; the
        # secondary id(s) get their own Citation with extra=None
        # (since the secondary form was just `#N`).
        result = find_citations("[problem #293, #294]")
        assert result[1].extra is None


class TestCitationFrozen:
    def test_citation_is_immutable(self) -> None:
        c = Citation(
            record_type="encounter",
            record_id="1",
            extra=None,
            raw="[encounter #1]",
        )
        with pytest.raises(Exception):  # noqa: B017 — frozen dataclass raises FrozenInstanceError
            c.record_id = "2"  # type: ignore[misc]
