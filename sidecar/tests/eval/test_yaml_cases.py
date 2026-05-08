"""Unit tests for the YAML eval case loader.

All tests are pure-unit — no LLM, no live stack.  They verify that the
five YAML case files under tests/eval/cases/ load correctly and conform
to the EvalCase schema contract.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.eval.harness import EvalCase, EvalCategory

# Locate the cases/ directory relative to this test file.
CASES_DIR = pathlib.Path(__file__).parent / "cases"

# All YAML files that must exist.
CASE_FILES = [
    "happy_path.yaml",
    "missing_data.yaml",
    "ambiguous.yaml",
    "unauthorized.yaml",
    "hallucination.yaml",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str) -> list[EvalCase]:
    """Load a YAML case file via the yaml_cases loader."""
    from tests.eval.yaml_cases import load_yaml_cases

    return load_yaml_cases(CASES_DIR / filename)


# ---------------------------------------------------------------------------
# 1. Each YAML file loads without errors and returns a non-empty list
# ---------------------------------------------------------------------------


class TestYamlFilesLoad:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_file_loads_and_is_non_empty(self, filename: str) -> None:
        """load_yaml_cases returns a non-empty list for every case file."""
        cases = _load(filename)
        assert isinstance(cases, list)
        assert len(cases) > 0, f"{filename} produced an empty list"

    def test_happy_path_has_three_cases(self) -> None:
        assert len(_load("happy_path.yaml")) == 3

    def test_missing_data_has_two_cases(self) -> None:
        assert len(_load("missing_data.yaml")) == 2

    def test_ambiguous_has_two_cases(self) -> None:
        assert len(_load("ambiguous.yaml")) == 2

    def test_unauthorized_has_two_cases(self) -> None:
        assert len(_load("unauthorized.yaml")) == 2

    def test_hallucination_has_three_cases(self) -> None:
        assert len(_load("hallucination.yaml")) == 3


# ---------------------------------------------------------------------------
# 2. All loaded cases have valid EvalCategory values
# ---------------------------------------------------------------------------


class TestCategoryValues:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_all_cases_have_valid_category(self, filename: str) -> None:
        """Every case's category is a valid EvalCategory member."""
        valid_values = {cat.value for cat in EvalCategory}
        for case in _load(filename):
            assert case.category.value in valid_values, (
                f"Case {case.id!r} in {filename} has invalid category: "
                f"{case.category!r}"
            )

    def test_happy_path_category_is_happy_path(self) -> None:
        for case in _load("happy_path.yaml"):
            assert case.category == EvalCategory.HAPPY_PATH

    def test_missing_data_category_is_missing_data(self) -> None:
        for case in _load("missing_data.yaml"):
            assert case.category == EvalCategory.MISSING_DATA

    def test_ambiguous_category_is_ambiguous(self) -> None:
        for case in _load("ambiguous.yaml"):
            assert case.category == EvalCategory.AMBIGUOUS

    def test_unauthorized_category_is_auth_boundary(self) -> None:
        for case in _load("unauthorized.yaml"):
            assert case.category == EvalCategory.AUTH_BOUNDARY

    def test_hallucination_category_is_hallucination(self) -> None:
        for case in _load("hallucination.yaml"):
            assert case.category == EvalCategory.HALLUCINATION


# ---------------------------------------------------------------------------
# 3. All loaded cases have non-empty id, query, expected_behavior
# ---------------------------------------------------------------------------


class TestRequiredFields:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_all_cases_have_non_empty_required_fields(self, filename: str) -> None:
        """id, query, and expected_behavior must be non-empty strings."""
        for case in _load(filename):
            assert case.id, f"Case in {filename} has empty id"
            assert case.query, f"Case {case.id!r} in {filename} has empty query"
            assert case.expected_behavior, (
                f"Case {case.id!r} in {filename} has empty expected_behavior"
            )

    def test_case_ids_are_unique_within_each_file(self) -> None:
        """No two cases in the same file share an id."""
        for filename in CASE_FILES:
            cases = _load(filename)
            ids = [c.id for c in cases]
            assert len(ids) == len(set(ids)), (
                f"Duplicate ids in {filename}: {ids}"
            )


# ---------------------------------------------------------------------------
# 4. Unknown category in YAML raises ValueError
# ---------------------------------------------------------------------------


class TestUnknownCategoryRaisesError:
    def test_unknown_category_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        """A YAML entry with an unrecognized category raises ValueError."""
        from tests.eval.yaml_cases import load_yaml_cases

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            "- id: bad_1\n"
            "  category: not_a_real_category\n"
            "  patient_id: 1\n"
            "  query: test\n"
            "  expected_behavior: test\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="not_a_real_category"):
            load_yaml_cases(bad_yaml)


# ---------------------------------------------------------------------------
# 5. Patient IDs are positive integers
# ---------------------------------------------------------------------------


class TestPatientIds:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_all_patient_ids_are_positive_integers(self, filename: str) -> None:
        """patient_id must be an int > 0 for every case."""
        for case in _load(filename):
            assert isinstance(case.patient_id, int), (
                f"Case {case.id!r} patient_id is not int: {case.patient_id!r}"
            )
            assert case.patient_id > 0, (
                f"Case {case.id!r} has non-positive patient_id: {case.patient_id}"
            )

    def test_happy_path_patient_id_is_8(self) -> None:
        for case in _load("happy_path.yaml"):
            assert case.patient_id == 8

    def test_missing_data_md1_patient_id_is_99(self) -> None:
        cases = {c.id: c for c in _load("missing_data.yaml")}
        assert cases["md_1"].patient_id == 99

    def test_missing_data_md2_patient_id_is_8(self) -> None:
        cases = {c.id: c for c in _load("missing_data.yaml")}
        assert cases["md_2"].patient_id == 8


# ---------------------------------------------------------------------------
# 6. grounding_check is not set (optional callable can't round-trip YAML)
# ---------------------------------------------------------------------------


class TestGroundingCheckAbsent:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_grounding_check_is_none_for_all_yaml_cases(self, filename: str) -> None:
        """YAML-loaded cases must not set grounding_check."""
        for case in _load(filename):
            assert case.grounding_check is None, (
                f"Case {case.id!r} unexpectedly has grounding_check set"
            )


# ---------------------------------------------------------------------------
# 7. tags field round-trips through YAML — used by the pre-commit eval-smoke
#    hook (Task 23) to select a 10-case representative subset
# ---------------------------------------------------------------------------


class TestTagsRoundTrip:
    @pytest.mark.parametrize("filename", CASE_FILES)
    def test_tags_default_to_empty_tuple_when_absent(self, filename: str) -> None:
        """Cases without an explicit ``tags:`` key get an empty tags tuple."""
        for case in _load(filename):
            # If the YAML omits tags, the loader should default to ().
            assert isinstance(case.tags, tuple), (
                f"Case {case.id!r} in {filename} has non-tuple tags: "
                f"{type(case.tags).__name__}"
            )

    def test_w1_cases_carry_no_eval_smoke_tags(self) -> None:
        """Smoke selection lives entirely in the W2 suite — keep W1 untagged.

        Task 23 acceptance: the pre-commit smoke suite is sourced from the
        50 W2 cases (``tests/eval/cases/week2/``), not the W1 set. This
        test fences off accidental drift.
        """
        all_w1 = [c for f in CASE_FILES for c in _load(f)]
        tagged = [c.id for c in all_w1 if "eval_smoke" in c.tags]
        assert tagged == [], (
            f"W1 cases should carry no eval_smoke tags; found: {tagged}"
        )

    def test_exactly_ten_w2_cases_tagged_eval_smoke(self) -> None:
        """The pre-commit smoke suite must be exactly 10 W2 cases (Task 23)."""
        from tests.eval.gate.runner_w2 import load_week2_cases

        smoke = [c for c in load_week2_cases() if "eval_smoke" in c.tags]
        assert len(smoke) == 10, (
            f"Expected 10 eval_smoke-tagged W2 cases, found {len(smoke)}: "
            f"{[c.id for c in smoke]}"
        )

    def test_w2_smoke_selection_is_two_per_category(self) -> None:
        """Each of the five W2 categories contributes exactly two smoke cases."""
        from collections import Counter

        from tests.eval.gate.runner_w2 import load_week2_cases

        smoke = [c for c in load_week2_cases() if "eval_smoke" in c.tags]
        per_cat = Counter(c.category for c in smoke)
        assert all(count == 2 for count in per_cat.values()), (
            f"eval_smoke distribution is not 2 per category: {per_cat}"
        )
        assert len(per_cat) == 5, (
            f"eval_smoke must cover all 5 W2 categories, got {len(per_cat)}: "
            f"{sorted(c.value for c in per_cat)}"
        )
