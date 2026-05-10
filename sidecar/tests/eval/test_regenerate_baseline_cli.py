"""Tests for the regenerate_baseline CLI.

The CLI is the manual entry point a human runs once with real Anthropic
credentials to overwrite the stub ``baselines/week2.json`` with measured
pass rates. Tests use mocked LLMs so the CLI exits cleanly without
burning tokens.

What's verified:
  * CLI accepts ``--output PATH`` and writes a baseline JSON.
  * Output schema: per-category pass rates + ``_meta`` block carrying
    ``status: "measured"``, a UTC timestamp, and the git sha.
  * Adapter wiring: when ``--mock`` is passed, the CLI uses a mock
    supervisor so the test suite can exercise the full pipeline without
    real LLM calls. Production runs omit ``--mock``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import patch

import pytest

from agentforge.eval.regenerate_baseline import build_arg_parser, run_cli


class TestArgParsing:
    def test_parser_requires_output(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_parser_accepts_output(self, tmp_path: pathlib.Path) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--output", str(tmp_path / "x.json")])
        assert args.output == tmp_path / "x.json"


class TestMockedRun:
    @pytest.mark.asyncio
    async def test_mock_run_writes_measured_baseline(
        self, tmp_path: pathlib.Path
    ) -> None:
        out = tmp_path / "week2.json"

        exit_code = await run_cli(["--output", str(out), "--mock"])

        assert exit_code == 0
        assert out.is_file()
        payload = json.loads(out.read_text(encoding="utf-8"))

        # Per-category rates must cover the five W2 categories.
        for cat in (
            "extraction",
            "evidence_retrieval",
            "citations",
            "refusal",
            "missing_data",
        ):
            assert cat in payload
            rate = payload[cat]
            assert isinstance(rate, float)
            assert 0.0 <= rate <= 1.0

        # _meta carries provenance info.
        meta = payload["_meta"]
        assert meta["status"] == "measured"
        assert "timestamp" in meta
        # Timestamp is ISO-8601 UTC.
        assert meta["timestamp"].endswith("Z") or "+00:00" in meta["timestamp"]
        assert "git_sha" in meta

    @pytest.mark.asyncio
    async def test_mock_run_pass_rates_reflect_supervisor(
        self, tmp_path: pathlib.Path
    ) -> None:
        # The mock supervisor returns a passing SupervisorOutput for every
        # case → every category should hit 1.0.
        out = tmp_path / "week2.json"

        exit_code = await run_cli(["--output", str(out), "--mock"])

        assert exit_code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        for cat in (
            "extraction",
            "evidence_retrieval",
            "citations",
            "refusal",
            "missing_data",
        ):
            # All-passing supervisor → category rate is exactly 1.0.
            assert payload[cat] == pytest.approx(1.0)


class TestSafetyDisclaimer:
    def test_module_docstring_documents_invocation(self) -> None:
        # Surface the exact command in the module docstring so a human
        # reading the file finds the invocation immediately.
        from agentforge.eval import regenerate_baseline

        doc = regenerate_baseline.__doc__ or ""
        assert "agentforge.eval.regenerate_baseline" in doc
        assert "--output" in doc


class TestRecordingFlag:
    """The ``--record`` flag enables fixture capture during a real-LLM run.

    These tests verify the CLI plumbing without firing real LLMs:
    parser surface, mock-mode rejection, and the default fixture
    directory resolution.
    """

    def test_parser_accepts_record_flag(self, tmp_path: pathlib.Path) -> None:
        parser = build_arg_parser()
        args = parser.parse_args([
            "--output", str(tmp_path / "x.json"),
            "--record",
        ])
        assert args.record is True
        assert args.record_dir is None  # falls back to default

    def test_parser_accepts_record_dir(self, tmp_path: pathlib.Path) -> None:
        parser = build_arg_parser()
        target = tmp_path / "fixtures"
        args = parser.parse_args([
            "--output", str(tmp_path / "x.json"),
            "--record",
            "--record-dir", str(target),
        ])
        assert args.record_dir == target

    @pytest.mark.asyncio
    async def test_record_with_mock_is_rejected(
        self, tmp_path: pathlib.Path
    ) -> None:
        """``--mock --record`` together is nonsensical — must error.

        Mock supervisor doesn't issue any LLM calls, so a recording
        run against it would write empty fixtures and silently
        mislead the operator. Refuse loudly instead.
        """
        out = tmp_path / "week2.json"
        with pytest.raises(RuntimeError, match="--record requires a real-LLM run"):
            await run_cli([
                "--output", str(out),
                "--mock",
                "--record",
                "--record-dir", str(tmp_path / "fixtures"),
            ])
