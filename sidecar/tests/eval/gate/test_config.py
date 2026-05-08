"""Tests for the eval-gate config loader (Task 18.1).

The loader parses ``sidecar/eval_config.yaml`` into a typed
:class:`EvalGateConfig` object so the runner / gate logic in later
subtasks can read thresholds without re-implementing YAML parsing.

Acceptance:
  * Default load (no args) finds ``sidecar/eval_config.yaml`` and
    surfaces the five category thresholds, the regression threshold,
    and the LLM-judge model + temperature.
  * Explicit-path load (used in tests) works on any YAML file matching
    the schema.
  * Invalid YAML — missing keys, out-of-range thresholds, wrong type —
    raises a structured ``ValueError`` rather than a low-level Pydantic
    error so the CI failure message is human-readable.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.eval.gate.config import (
    EvalGateConfig,
    load_eval_gate_config,
)


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "eval_config.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    return target


VALID_YAML = """
category_thresholds:
  schema_valid: 0.9
  citation_present: 0.9
  factually_consistent: 0.9
  safe_refusal: 0.9
  no_phi_in_logs: 0.9
regression_threshold: 0.05
llm_judge_model: claude-sonnet-4-6
llm_judge_temperature: 0
"""


class TestLoaderHappyPath:
    def test_loader_returns_typed_config_from_explicit_path(
        self, tmp_path: Path
    ) -> None:
        path = _write(tmp_path, VALID_YAML)
        cfg = load_eval_gate_config(path)
        assert isinstance(cfg, EvalGateConfig)
        assert cfg.category_thresholds["schema_valid"] == 0.9
        assert cfg.category_thresholds["citation_present"] == 0.9
        assert cfg.category_thresholds["factually_consistent"] == 0.9
        assert cfg.category_thresholds["safe_refusal"] == 0.9
        assert cfg.category_thresholds["no_phi_in_logs"] == 0.9
        assert cfg.regression_threshold == 0.05
        assert cfg.llm_judge_model == "claude-sonnet-4-6"
        assert cfg.llm_judge_temperature == 0

    def test_default_load_finds_repo_eval_config(self) -> None:
        # The shipped sidecar/eval_config.yaml must exist and parse.
        cfg = load_eval_gate_config()
        # Sanity: the five required category thresholds are present and
        # at the spec's minimum (0.9 each).
        for category in (
            "schema_valid",
            "citation_present",
            "factually_consistent",
            "safe_refusal",
            "no_phi_in_logs",
        ):
            assert category in cfg.category_thresholds
            assert cfg.category_thresholds[category] >= 0.9


class TestLoaderRejectsBadInput:
    def test_missing_category_threshold_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        body = """
        category_thresholds:
          schema_valid: 0.9
          citation_present: 0.9
          factually_consistent: 0.9
          safe_refusal: 0.9
          # no_phi_in_logs missing
        regression_threshold: 0.05
        llm_judge_model: claude-sonnet-4-6
        llm_judge_temperature: 0
        """
        path = _write(tmp_path, body)
        with pytest.raises(ValueError, match="no_phi_in_logs"):
            load_eval_gate_config(path)

    def test_threshold_above_one_raises_value_error(self, tmp_path: Path) -> None:
        body = """
        category_thresholds:
          schema_valid: 1.5
          citation_present: 0.9
          factually_consistent: 0.9
          safe_refusal: 0.9
          no_phi_in_logs: 0.9
        regression_threshold: 0.05
        llm_judge_model: claude-sonnet-4-6
        llm_judge_temperature: 0
        """
        path = _write(tmp_path, body)
        with pytest.raises(ValueError, match="schema_valid"):
            load_eval_gate_config(path)

    def test_negative_regression_threshold_raises(self, tmp_path: Path) -> None:
        body = """
        category_thresholds:
          schema_valid: 0.9
          citation_present: 0.9
          factually_consistent: 0.9
          safe_refusal: 0.9
          no_phi_in_logs: 0.9
        regression_threshold: -0.1
        llm_judge_model: claude-sonnet-4-6
        llm_judge_temperature: 0
        """
        path = _write(tmp_path, body)
        with pytest.raises(ValueError, match="regression_threshold"):
            load_eval_gate_config(path)

    def test_missing_yaml_file_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(FileNotFoundError):
            load_eval_gate_config(missing)

    def test_unexpected_top_level_key_raises(self, tmp_path: Path) -> None:
        body = """
        category_thresholds:
          schema_valid: 0.9
          citation_present: 0.9
          factually_consistent: 0.9
          safe_refusal: 0.9
          no_phi_in_logs: 0.9
        regression_threshold: 0.05
        llm_judge_model: claude-sonnet-4-6
        llm_judge_temperature: 0
        unknown_field: oops
        """
        path = _write(tmp_path, body)
        with pytest.raises(ValueError, match="unknown_field"):
            load_eval_gate_config(path)
