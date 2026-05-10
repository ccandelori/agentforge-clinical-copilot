"""Unit tests for :class:`ReplaySupervisor`.

The end-to-end gate behaviour is covered by
:mod:`tests.eval.gate.test_gate_blocks_regression_replay`. These tests
isolate the supervisor's contract: it picks the right fixture per
case_id, applies the response transform, and surfaces a sane
SupervisorOutput shape.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agentforge.eval.replay import (
    ReplayCaseContext,
    ReplaySupervisor,
    default_intake_citation,
)
from agentforge.eval.seed_fixtures import _build_record, SyntheticFixture
from tests.eval.harness import EvalCase, EvalCategory


def _write_fixture(
    case_id: str, response_text: str, sources: str, tmp_path: pathlib.Path
) -> pathlib.Path:
    """Write a one-line synthetic fixture file and return its path."""
    fx = SyntheticFixture(
        case_id=case_id,
        canned_sources=sources,
        response_text=response_text,
    )
    record = _build_record(fx)
    path = tmp_path / f"{case_id}.jsonl"
    path.write_text(record.to_jsonl() + "\n", encoding="utf-8")
    return path


def _fake_case(case_id: str) -> EvalCase:
    """Build a minimal EvalCase whose query matches what the seed
    expects (the seed pulls query text from the YAML loader; tests
    bypass that by faking the EvalCase directly)."""
    # The replay supervisor uses ``case.query`` as the synthesizer's
    # user message, so we need that to be exactly what the seed used
    # when computing the request hash. We piggy-back on _load_case_query
    # so the fake_case mirrors the real loader.
    from agentforge.eval.seed_fixtures import _load_case_query

    return EvalCase(
        id=case_id,
        category=EvalCategory.CITATIONS,
        patient_id=8,
        query=_load_case_query(case_id),
        expected_behavior="(test fixture)",
    )


class TestReplaySupervisor:
    async def test_returns_recorded_response_text(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Seed expects (case query, sources). Use the real seed shape
        # for w2_cit_01 so the request hash matches.
        sources = (
            "EXTRACTION:\n"
            '{"chief_concern": "Hypertension"}\n\n'
            "EVIDENCE:\n[guideline #abc] (lipids-aha-acc-2018) Statin "
            "benefit groups include diabetes mellitus."
        )
        fixture_path = _write_fixture(
            "w2_cit_01",
            response_text="hypertension [problem #5]",
            sources=sources,
            tmp_path=tmp_path,
        )
        sup = ReplaySupervisor()
        sup.register(
            ReplayCaseContext(
                case_id="w2_cit_01",
                fixture_path=fixture_path,
                canned_sources=sources,
                canned_citations=(default_intake_citation(),),
            )
        )
        case = _fake_case("w2_cit_01")
        output = await sup(case)
        assert output.response == "hypertension [problem #5]"
        assert output.structured_citations
        assert output.sources == sources

    async def test_response_transform_applied(
        self, tmp_path: pathlib.Path
    ) -> None:
        sources = "EVIDENCE:\n[guideline #abc]"
        fixture_path = _write_fixture(
            "w2_cit_01",
            response_text="cited [problem #5]",
            sources=sources,
            tmp_path=tmp_path,
        )
        sup = ReplaySupervisor(response_transform=lambda t: t.upper())
        sup.register(
            ReplayCaseContext(
                case_id="w2_cit_01",
                fixture_path=fixture_path,
                canned_sources=sources,
                canned_citations=(default_intake_citation(),),
            )
        )
        output = await sup(_fake_case("w2_cit_01"))
        assert output.response == "CITED [PROBLEM #5]"

    async def test_citations_transform_applied(
        self, tmp_path: pathlib.Path
    ) -> None:
        sources = "EVIDENCE:\n[guideline #abc]"
        fixture_path = _write_fixture(
            "w2_cit_01",
            response_text="cited [problem #5]",
            sources=sources,
            tmp_path=tmp_path,
        )
        sup = ReplaySupervisor(citations_transform=lambda _: ())
        sup.register(
            ReplayCaseContext(
                case_id="w2_cit_01",
                fixture_path=fixture_path,
                canned_sources=sources,
                canned_citations=(default_intake_citation(),),
            )
        )
        output = await sup(_fake_case("w2_cit_01"))
        assert output.structured_citations == ()

    async def test_unregistered_case_raises(self, tmp_path: pathlib.Path) -> None:
        sup = ReplaySupervisor()
        case = _fake_case("w2_cit_01")
        with pytest.raises(KeyError, match="no recorded context for case"):
            await sup(case)
