"""Candidate identities and node scores match actually executed upstream C++."""

import json
from pathlib import Path
from typing import Any

import pytest

from reference.placement_candidate import (
    candidate_result,
    compare_result,
    load_cases,
)

REFERENCE = Path(__file__).parents[1] / "reference"
EXPECTED = json.loads(
    (REFERENCE / "placement_expected.json").read_text(encoding="utf-8")
)["cases"]
CASES = load_cases()


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if not case.get("known_difference")],
    ids=lambda case: case["name"],
)
def test_original_geometry_candidates_scores_and_final_placement(
    case: dict[str, Any],
) -> None:
    """Use raw C++ oracles; compare candidate order and every observed score path."""
    expected = EXPECTED[case["name"]]
    actual = candidate_result(case, expected)

    compare_result(expected, actual)


def test_next_slm_row_reset_correction_is_an_explicit_source_difference() -> None:
    case = next(
        case for case in CASES if case["name"] == "initial_cross_slm_source_defect"
    )
    expected = EXPECTED[case["name"]]
    actual = candidate_result(case, expected)

    assert expected["initial"] == [[0, 0, 0], [0, 0, 1], [3, 0, 0]]
    assert actual["initial"] == [[0, 0, 0], [0, 0, 1], [3, 2, 0]]


def test_full_nearest_column_boolean_bug_is_an_explicit_source_difference() -> None:
    case = next(
        case
        for case in CASES
        if case["name"] == "storage_full_nearest_column_source_defect"
    )
    expected = EXPECTED[case["name"]]
    actual = candidate_result(case, expected)

    compare_result(expected["jobs"], actual["jobs"])
    assert expected["scores"][0]["scale"] == [1, 1]
    assert actual["scores"][0]["scale"] == pytest.approx([1, 4 / 3])
    assert actual["final"] != expected["final"]
    assert len({tuple(site) for site in actual["final"]}) == len(case["placement"])


def test_straddled_y_underflow_is_an_explicit_source_difference() -> None:
    case = next(
        case
        for case in CASES
        if case["name"] == "nearest_gate_straddled_y_source_defect"
    )
    expected = EXPECTED[case["name"]]
    actual = candidate_result(case, expected)

    assert expected["nearest_entanglement"] == [[3, 0, 0]]
    assert actual["nearest_entanglement"] == [[1, 0, 0]]
