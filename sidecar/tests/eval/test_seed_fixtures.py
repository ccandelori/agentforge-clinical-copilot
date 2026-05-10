"""Unit tests for the synthetic-seed fixture generator."""

from __future__ import annotations

import json
import pathlib

import pytest

from agentforge.eval.seed_fixtures import (
    SEED_FIXTURES,
    SyntheticFixture,
    _build_record,
    _load_case_query,
    main,
    write_index,
    write_seed_fixtures,
)
from agentforge.llm.recording import RecordedCall


class TestSyntheticFixture:
    def test_seed_covers_every_w2_category(self) -> None:
        """Defensive: drop a category from the seed → CI can't catch
        a regression in that category. Pin coverage at the seed level.
        """
        prefixes = {fx.case_id.rsplit("_", 1)[0] for fx in SEED_FIXTURES}
        # The W2 case YAML uses these prefixes for the five categories.
        expected = {"w2_cit", "w2_evr", "w2_ext", "w2_md", "w2_ref"}
        missing = expected - prefixes
        assert not missing, f"seed missing categories: {missing}"

    def test_every_seed_case_id_resolves_to_a_yaml_case(self) -> None:
        """Each seed entry must correspond to a real W2 case yaml entry.

        Otherwise the seed fixture is dead weight — the replay supervisor
        won't be asked to grade a case that isn't in the suite.
        """
        for fixture in SEED_FIXTURES:
            query = _load_case_query(fixture.case_id)
            assert query, f"empty query for case {fixture.case_id}"

    def test_unknown_case_id_raises(self) -> None:
        with pytest.raises(KeyError, match="no W2 case has that id"):
            _load_case_query("does-not-exist-w2_xxx_99")


class TestBuildRecord:
    def test_record_carries_label(self) -> None:
        fixture = SyntheticFixture(
            case_id="w2_cit_01",
            canned_sources="EVIDENCE:\n[guideline #x]",
            response_text="cited [med #1]",
        )
        record = _build_record(fixture)
        assert isinstance(record, RecordedCall)
        assert "case_id=w2_cit_01" in record.label
        assert "node=synthesizer" in record.label
        assert record.response.text == "cited [med #1]"
        # Hash is the canonical sha256 hex (64 chars).
        assert len(record.request_hash) == 64

    def test_record_round_trips_through_jsonl(self) -> None:
        fixture = SEED_FIXTURES[0]
        record = _build_record(fixture)
        line = record.to_jsonl()
        parsed = RecordedCall.from_jsonl(line)
        assert parsed.request_hash == record.request_hash
        assert parsed.response.text == record.response.text


class TestWriteSeedFixtures:
    def test_writes_one_file_per_seed_entry(self, tmp_path: pathlib.Path) -> None:
        count = write_seed_fixtures(tmp_path)
        assert count == len(SEED_FIXTURES)
        files = sorted(p.name for p in tmp_path.glob("*.jsonl"))
        expected = sorted(f"{fx.case_id}.jsonl" for fx in SEED_FIXTURES)
        assert files == expected

    def test_index_lists_every_fixture(self, tmp_path: pathlib.Path) -> None:
        write_seed_fixtures(tmp_path)
        index_path = write_index(tmp_path)
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        assert payload["fixture_kind"] == "synthetic-seed"
        assert {c["case_id"] for c in payload["cases"]} == {
            fx.case_id for fx in SEED_FIXTURES
        }


class TestMainCli:
    def test_cli_writes_fixtures(self, tmp_path: pathlib.Path) -> None:
        exit_code = main(["--output", str(tmp_path)])
        assert exit_code == 0
        # Every seed fixture should now exist on disk.
        for fx in SEED_FIXTURES:
            assert (tmp_path / f"{fx.case_id}.jsonl").is_file()
        assert (tmp_path / "index.json").is_file()
