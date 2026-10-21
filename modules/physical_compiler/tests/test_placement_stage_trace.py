"""Frozen stage extraction and choice comparison across equal-distance ties."""

from typing import Any

from reference.placement_trace_candidate import (
    extract_stage,
    first_layout_difference,
    normalize_path,
)


def test_storage_stage_uses_original_gate_output_and_resolved_next_layer() -> None:
    source: dict[str, Any] = {
        "placements": [
            [[0, 0, 0], [0, 0, 1]],
            [[1, 0, 0], [2, 0, 0]],
            [[1, 0, 0], [0, 0, 1]],
            [[1, 0, 0], [2, 0, 0]],
            [[0, 0, 0], [0, 0, 1]],
        ],
        "two_layers": [[[0, 1]], [[0, 1]]],
        "reuse": [[0, 1]],
    }
    candidate = {**source, "placements": [*source["placements"][:2], [], [], []]}
    assert first_layout_difference(source, candidate) == 2
    stage = extract_stage(
        {"name": "example", "slms": [], "aod": {"min_spacing": 2}},
        source,
        2,
        {"method": "ids"},
    )
    assert stage["stage"] == "storage"
    assert stage["placement"] == source["placements"][1]
    assert stage["expected_final"] == source["placements"][2]
    assert stage["next_gates"] == [[0, 1]]
    assert stage["previous_reuse"] == []
    assert stage["next_reuse"] == [0, 1]
    assert source["placements"][2] == [[1, 0, 0], [0, 0, 1]]


def test_same_assignments_are_compared_after_option_index_normalization() -> None:
    source = ((((0, 1),), ((0, 2),)), ((), ((1, 4),)))
    candidate = ((((0, 2),), ((0, 1),)), (((1, 4),), ()))
    assert normalize_path([0, 1], source) == normalize_path([1, 0], candidate)
    assert normalize_path([0, 0], source) != normalize_path([0, 0], candidate)
