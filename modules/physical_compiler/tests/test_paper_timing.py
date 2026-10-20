"""Exact Search Smarter evaluation timing, independent of placement scores."""

import json
from typing import Any

import pytest
from test_placement_scoring import _rules

from nadit_physical_compiler.device import Position, Timing, loads_device
from nadit_physical_compiler.transport.rules import MoveRequest, StrictRules


@pytest.mark.parametrize("distance", [0, 1, 13.75, 109.999, 110, 110.001, 220, 1000])
def test_paper_profile_matches_original_evaluation_formula(distance: float) -> None:
    jerk = 32 * 110 / 200**3
    expected = (
        2 * (4 * distance / jerk) ** (1 / 3)
        if distance <= 110
        else 200 + (distance - 110) / 1.1
    )
    assert Timing(15, 16, 99, "paper").move_duration(distance) == pytest.approx(
        expected
    )


def test_paper_profile_boundary_and_existing_linear_default() -> None:
    assert Timing(1, 2, 3).move_duration(110) == 330
    assert Timing(1, 2, 3, "paper").move_duration(110) == 200
    assert Timing(1, 2, 3, "paper").move_duration(220) == 300


@pytest.mark.parametrize("distance", [-1, float("inf"), float("nan")])
def test_profile_reuses_distance_validation(distance: float) -> None:
    with pytest.raises(ValueError, match="distance"):
        Timing(0, 0, 0, "paper").move_duration(distance)


def test_json_profile_is_optional_and_transfer_fields_are_preserved() -> None:
    data: dict[str, Any] = {
        "name": "timing",
        "zones": [],
        "aods": [],
        "timing": {
            "load_us": 15,
            "store_us": 16,
            "move_us_per_um": 0,
            "move_profile": "paper",
        },
    }
    timing = loads_device(json.dumps(data)).timing
    assert timing.load_us == 15 and timing.store_us == 16
    assert timing.move_duration(220) == 300
    del data["timing"]["move_profile"]
    assert loads_device(json.dumps(data)).timing.move_profile == "linear"


def test_rules_apply_profile_to_the_longest_move_once() -> None:
    rules = StrictRules(_rules().aod, Timing(15, 16, 0, "paper"))
    moves = (
        MoveRequest(0, Position(0, 0), Position(0, 110)),
        MoveRequest(1, Position(5, 0), Position(5, 55)),
    )
    assert rules.duration_us(moves) == 231


@pytest.mark.parametrize("region", ["omitted", None, [0, 0, 400, 400]])
def test_optional_global_region_json(region: str | list[int] | None) -> None:
    control: dict[str, Any] = {
        "channel": "global",
        "rotation_us": 52,
        "holders": ["slm"],
    }
    if region != "omitted":
        control["region"] = region
    device = loads_device(
        json.dumps(
            {
                "name": "global",
                "zones": [],
                "aods": [],
                "timing": {"load_us": 15, "store_us": 15, "move_us_per_um": 1},
                "global_control": control,
            }
        )
    )
    assert device.global_control is not None
    assert device.global_control.holders == ("slm",)
    assert device.global_control.region == (
        tuple(region) if isinstance(region, list) else None
    )
