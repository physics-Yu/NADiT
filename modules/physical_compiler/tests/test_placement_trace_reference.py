"""Observation injection fails on source drift and preserves stage input."""

import pytest

from reference.placement_reference import fixture_input as original_input
from reference.placement_trace_reference import _replace_once, fixture_input


@pytest.mark.parametrize("source", ["different source", "point point"])
def test_observer_refuses_missing_or_ambiguous_injection_point(source: str) -> None:
    with pytest.raises(ValueError, match="exactly one source observation point"):
        _replace_once(source, "point", "observe; point")


def test_observer_keeps_original_statement_after_observation() -> None:
    assert _replace_once("before; point; after", "point", "observe; point") == (
        "before; observe; point; after"
    )


def test_stage_input_adds_search_dispatch_without_changing_original_fields() -> None:
    case = {
        "slms": [
            {
                "id": 0,
                "rows": 3,
                "columns": 4,
                "pitch": [8, 8],
                "origin": [0, 0],
            }
        ],
        "stage": "storage",
        "placement": [[0, 2, 0], [0, 2, 1]],
        "gates": [[0, 1]],
        "config": {"method": "astar", "max_nodes": 345},
    }
    supplied = fixture_input(case).splitlines()
    assert supplied.pop(3) == "astar 345"
    assert supplied == original_input(case).splitlines()
