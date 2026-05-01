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
