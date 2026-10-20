"""Device geometry, configuration, and distance-only timing tests."""

import json
from dataclasses import FrozenInstanceError, replace
from math import inf, nan
from pathlib import Path

import pytest

from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
    load_device,
    loads_device,
    slm_grid,
)

FIXTURE = Path(__file__).parent / "fixtures" / "devices" / "zoned_small.json"


def test_position_is_an_immutable_value() -> None:
    position = Position(1, 2)
    assert position == Position(1.0, 2.0)
    assert hash(position) == hash(Position(1, 2))
    with pytest.raises(FrozenInstanceError):
        position.x = 3  # type: ignore[misc]


@pytest.mark.parametrize("value", [inf, -inf, nan])
def test_nonfinite_coordinates_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        Position(value, 0)
    with pytest.raises(ValueError, match="finite"):
        Region(0, 0, 10, value)


def test_region_contains_edges_and_single_rows() -> None:
    bounds = Region(0, 0, 10, 5)
    assert bounds.contains(Position(0, 0))
    assert bounds.contains(Position(10, 5))
    assert bounds.contains(Position(4, 2))
    assert not bounds.contains(Position(10.1, 5))
    assert not bounds.contains(Position(0, -0.1))
    assert Region(0, 120, 18, 120).contains(Position(8, 120))


@pytest.mark.parametrize(
    "bounds",
    [(2, 0, 1, 3), (0, 2, 3, 1)],
)
def test_reversed_bounds_are_rejected(bounds: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="bounds"):
        Region(*bounds)


def test_grid_generates_stable_row_major_site_ids_and_positions() -> None:
    sites = slm_grid("storage", Position(10, 20), 2, 3, Position(5, 5))
    assert sites == (
        SLMSite("storage_r0_c0", Position(10, 20)),
        SLMSite("storage_r0_c1", Position(15, 20)),
        SLMSite("storage_r0_c2", Position(20, 20)),
        SLMSite("storage_r1_c0", Position(10, 25)),
        SLMSite("storage_r1_c1", Position(15, 25)),
        SLMSite("storage_r1_c2", Position(20, 25)),
    )


@pytest.mark.parametrize(
    ("rows", "columns", "pitch"),
    [(0, 2, Position(5, 5)), (2, -1, Position(5, 5)), (2, 2, Position(0, 5))],
)
def test_grid_rejects_nonpositive_dimensions_and_pitch(
    rows: int, columns: int, pitch: Position
) -> None:
    with pytest.raises(ValueError, match="positive"):
        slm_grid("storage", Position(0, 0), rows, columns, pitch)


def test_fixture_loads_generated_storage_and_explicit_gate_sites() -> None:
    device = load_device(FIXTURE)
    assert device.name == "zoned_small"
    assert len(device.slm_sites) == 12
    assert device.site("storage_r1_c2").position == Position(10, 5)
    assert device.site("gate_1_right").position == Position(10, 120)
    assert device.aod("transport").max_rows is None
    assert device.aod("transport").max_columns is None
    assert device.zones_at(Position(8, 120), "cz")[0].id == "entanglement"
    assert device.zones_at(Position(8, 240), "measure")[0].id == "measurement"
    assert not any(site.position.y == 240 for site in device.slm_sites)
    assert device == loads_device(FIXTURE.read_text(encoding="utf-8"))


def test_zone_queries_allow_overlap_and_filter_operations() -> None:
    bounds = Region(0, 0, 10, 10)
    storage = Zone("storage", bounds, ("store",))
    gates = Zone("entanglement", bounds, ("cz", "rotate"))
    device = Device("shared", (storage, gates), (), (), Timing(1, 2, 3))
    assert device.zones_at(Position(5, 5)) == (storage, gates)
    assert device.zones_at(Position(5, 5), "cz") == (gates,)
    assert device.zones_at(Position(5, 5), "measure") == ()
    assert device.zones_at(Position(11, 5)) == ()


def test_unknown_site_or_aod_reports_the_missing_id() -> None:
    device = load_device(FIXTURE)
    with pytest.raises(KeyError, match="missing_site"):
        device.site("missing_site")
    with pytest.raises(KeyError, match="missing_aod"):
        device.aod("missing_aod")


@pytest.mark.parametrize("collection", ["zones", "slm_sites", "aods"])
def test_device_ids_are_unique_within_each_collection(collection: str) -> None:
    device = load_device(FIXTURE)
    with pytest.raises(ValueError, match="Duplicate"):
        if collection == "zones":
            replace(device, zones=(device.zones[0], device.zones[0]))
        elif collection == "slm_sites":
            replace(device, slm_sites=(device.slm_sites[0], device.slm_sites[0]))
        else:
            replace(device, aods=(device.aods[0], device.aods[0]))


@pytest.mark.parametrize(
    ("spacing", "rows", "columns"),
    [(0.0, None, None), (inf, None, None), (1.0, 0, None), (1.0, None, -1)],
)
def test_aod_spacing_and_optional_capacity_are_positive(
    spacing: float, rows: int | None, columns: int | None
) -> None:
    with pytest.raises(ValueError, match="positive"):
        AOD("transport", Region(0, 0, 20, 20), spacing, rows, columns)


def test_finite_aod_capacity_is_preserved() -> None:
    aod = AOD("transport", Region(0, 0, 20, 20), 1, 16, 16)
    assert (aod.max_rows, aod.max_columns) == (16, 16)


def test_timing_separates_transfer_overhead_from_linear_movement() -> None:
    timing = Timing(load_us=10, store_us=20, move_us_per_um=2)
    assert timing.move_duration(0) == 0
    assert timing.move_duration(5) == 10
    assert timing.load_us + timing.move_duration(5) + timing.store_us == 40
    assert Timing(0, 0, 0).move_duration(10) == 0


@pytest.mark.parametrize("value", [-1, inf, nan])
def test_timing_rejects_invalid_parameters_and_distance(value: float) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        Timing(value, 0, 1)
    with pytest.raises(ValueError, match="nonnegative"):
        Timing(0, value, 1)
    with pytest.raises(ValueError, match="nonnegative"):
        Timing(0, 0, value)
    with pytest.raises(ValueError, match="nonnegative"):
        Timing(1, 2, 3).move_duration(value)


def test_explicit_site_only_json_configuration() -> None:
    source = json.dumps(
        {
            "name": "explicit",
            "zones": [],
            "slm_sites": [{"id": "s0", "position": {"x": 3, "y": 4}}],
            "aods": [],
            "timing": {"load_us": 1, "store_us": 2, "move_us_per_um": 3},
        }
    )
    assert loads_device(source).site("s0") == SLMSite("s0", Position(3, 4))


def test_generated_and_explicit_site_ids_share_one_namespace() -> None:
    config = json.loads(FIXTURE.read_text(encoding="utf-8"))
    config["slm_sites"].append({"id": "storage_r0_c0", "position": {"x": 30, "y": 0}})
    with pytest.raises(ValueError, match="Duplicate"):
        loads_device(json.dumps(config))
