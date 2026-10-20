"""Source nearest-SLM selection, projection, and explicit geometry handoff."""

from dataclasses import replace
from math import hypot

import pytest

from nadit_physical_compiler.capabilities import CZCapability
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
    slm_grid,
)
from nadit_physical_compiler.placement.catalog import GatePairCatalog
from nadit_physical_compiler.placement.geometry import PlacementGeometry, SLMArray
from nadit_physical_compiler.transport.staging_geometry import (
    SiteStagingGeometry,
    StagingGeometry,
)

_STORAGE_PITCH = Position(5, 5)
_GATE_ORIGIN = Position(0, 100)


def _array(
    identifier: str,
    origin: Position,
    rows: int = 1,
    columns: int = 2,
    pitch: Position = _STORAGE_PITCH,
) -> SLMArray:
    sites = slm_grid(identifier, origin, rows, columns, pitch)
    grid = tuple(sites[row * columns : (row + 1) * columns] for row in range(rows))
    return SLMArray(identifier, "storage", grid, pitch)


def _gates(
    origin: Position = _GATE_ORIGIN,
    rows: int = 1,
    columns: int = 3,
    name: str = "g",
) -> tuple[SLMArray, SLMArray]:
    left = _array(f"{name}l", origin, rows, columns, Position(8, 20))
    right = _array(
        f"{name}r", Position(origin.x + 2, origin.y), rows, columns, Position(8, 20)
    )
    return (
        SLMArray(left.id, "left", left.sites, left.pitch, right.sites),
        SLMArray(right.id, "right", right.sites, right.pitch, left.sites),
    )


def test_array_rounds_half_up_and_clamps_to_last_trap() -> None:
    array = _array("s", Position(10, 20), 3, 3, Position(5, 10))
    assert array.project(Position(12.5, 25)) == (1, 1)
    assert array.project(Position(-100, -100)) == (0, 0)
    assert array.project(Position(100, 100)) == (2, 2)
    assert array.origin == Position(10, 20)
    assert (array.n_rows, array.n_cols) == (3, 3)


def test_storage_query_selects_rectangle_before_site_distance() -> None:
    wide = _array("wide", Position(0, 0), pitch=Position(10, 5))
    closer_site = _array("closer", Position(4, 0), columns=1)
    geometry = PlacementGeometry((wide, closer_site), (_gates(),))
    array, row, column = geometry.nearest_storage(Position(5, 0))
    assert array.id == "wide"
    assert array.site(row, column).position == Position(10, 0)


def test_storage_tie_retains_array_declaration_order() -> None:
    first = _array("first", Position(0, 0), columns=1)
    second = _array("second", Position(10, 0), columns=1)
    geometry = PlacementGeometry((second, first), (_gates(),))
    assert geometry.nearest_storage(Position(5, 20))[0].id == "second"


def test_gate_query_canonicalizes_sites_and_projects_first_not_midpoint() -> None:
    storage = _array("s", Position(0, 0), columns=5)
    geometry = PlacementGeometry((storage,), (_gates(),))
    first = storage.site(0, 0).position
    second = storage.site(0, 4).position
    selected = geometry.nearest_entanglement(second, first)
    assert selected[0].id == "gl"
    assert selected[1:] == (0, 0)
    assert selected is geometry.nearest_entanglement(first, second)
    assert selected[0].gate_pair(0, 0)[1].position == Position(2, 100)


def test_gate_query_can_select_right_array() -> None:
    storage = _array("s", Position(10, 0))
    geometry = PlacementGeometry((storage,), (_gates(columns=2),))
    array, row, column = geometry.nearest_entanglement(
        storage.site(0, 0).position, storage.site(0, 1).position
    )
    assert array.id == "gr"
    assert (row, column) == (0, 1)
    assert array.gate_pair(row, column)[0].position == Position(10, 100)
    assert array.gate_pair(row, column)[1].position == Position(8, 100)


def test_gate_query_compares_multiple_slms_and_uses_storage_declaration_order() -> None:
    first = _array("first", Position(100, 0))
    second = _array("second", Position(0, 0))
    geometry = PlacementGeometry(
        (first, second),
        (_gates(Position(0, 200), name="far"), _gates(Position(0, 100), name="near")),
    )
    array, row, column = geometry.nearest_entanglement(
        second.site(0, 0).position, first.site(0, 0).position
    )
    assert array.id == "nearl"
    assert (row, column) == (0, 2)


def test_straddling_rows_use_same_side_test_without_unsigned_underflow() -> None:
    storage = _array("s", Position(0, 0), rows=3, columns=1, pitch=Position(5, 20))
    geometry = PlacementGeometry(
        (storage,),
        (_gates(Position(0, 10), name="first"), _gates(Position(0, 30), name="second")),
    )
    # Both zones lie between the inputs: their lower bounds tie at 40.
    # The source one-sided y test wraps 40 + 2 * (10 - 40) to size_t max - 19.
    array, _, _ = geometry.nearest_entanglement(
        storage.site(0, 0).position, storage.site(2, 0).position
    )
    assert array.id == "firstl"


def test_nearest_gate_distance_same_row_max_other_rows_sum() -> None:
    storage = _array("s", Position(0, 0), rows=2)
    geometry = PlacementGeometry((storage,), (_gates(),))
    origin = storage.site(0, 0).position
    assert geometry.nearest_entanglement_distance(
        origin, storage.site(0, 1).position
    ) == pytest.approx(hypot(5, 100))
    assert geometry.nearest_entanglement_distance(
        origin, storage.site(1, 1).position
    ) == pytest.approx(100 + hypot(5, 95))


def _device() -> tuple[Device, GatePairCatalog]:
    storage = slm_grid("s", Position(0, 0), 2, 3, Position(5, 5))
    gates = tuple(
        site for array in _gates(columns=2) for row in array.sites for site in row
    )
    device = Device(
        "geometry",
        (
            Zone("storage", Region(-1, -1, 11, 6), ("rotate",)),
            Zone("gate", Region(-1, 99, 11, 101), ("cz",)),
        ),
        storage + gates + (SLMSite("spectator", Position(5, 100)),),
        (AOD("a", Region(-10, -10, 30, 200), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )
    catalog = GatePairCatalog.from_device(
        device, "gate", (("gl_r0_c0", "gr_r0_c0"), ("gl_r0_c1", "gr_r0_c1"))
    )
    return device, catalog


def test_inference_uses_catalog_arrays_and_ignores_gate_spectator() -> None:
    device, catalog = _device()
    geometry = PlacementGeometry.from_device(device, "a", catalog)
    storage = geometry.storage_arrays[0]
    assert (storage.n_rows, storage.n_cols, storage.pitch) == (2, 3, Position(5, 5))
    gate = geometry.entanglement_arrays[0][0]
    assert (gate.n_rows, gate.n_cols, gate.pitch) == (1, 2, Position(8, 2))
    assert gate.gate_pair(0, 1) == catalog.pairs[1]
    assert geometry.locate("s_r1_c2") == (storage, 1, 2)
    assert geometry.site_location(Position(10, 5)) == (storage, 1, 2)
    with pytest.raises(KeyError):
        geometry.locate("spectator")


def test_inference_respects_explicit_singleton_pitch() -> None:
    device, catalog = _device()
    device = replace(
        device,
        slm_sites=tuple(
            site
            for site in device.slm_sites
            if not site.id.startswith("s_") or site.id == "s_r0_c0"
        ),
    )
    staging = StagingGeometry(
        {
            "s_r0_c0": SiteStagingGeometry(
                "storage", 17, 23, 0, 17, 0, 23, 0, "storage"
            ),
            "gl_r0_c0": SiteStagingGeometry("left", 8, 29, 0, 16, 100, 129, 2, "gate"),
        }
    )
    geometry = PlacementGeometry.from_device(device, "a", catalog, staging=staging)
    assert geometry.storage_arrays[0].pitch == Position(17, 23)
    assert geometry.entanglement_arrays[0][0].pitch == Position(8, 29)


def test_inference_preserves_requested_multiple_storage_zone_order() -> None:
    device, catalog = _device()
    device = replace(
        device,
        zones=(*device.zones, Zone("second", Region(40, 0, 45, 0), ("rotate",))),
        slm_sites=(
            *device.slm_sites,
            *slm_grid("s2", Position(40, 0), 1, 2, Position(5, 5)),
        ),
    )
    geometry = PlacementGeometry.from_device(
        device, "a", catalog, ("second", "storage")
    )
    assert tuple(array.id for array in geometry.storage_arrays) == ("second", "storage")
    assert geometry.storage_arrays[0].site(0, 1).id == "s2_r0_c1"


def test_nonrectangular_flat_sites_require_explicit_array_grouping() -> None:
    device, catalog = _device()
    device = replace(
        device,
        slm_sites=tuple(site for site in device.slm_sites if site.id != "s_r1_c2"),
    )
    with pytest.raises(ValueError, match="explicit PlacementGeometry"):
        PlacementGeometry.from_device(device, "a", catalog)


def test_missing_site_query_reports_unknown_identity() -> None:
    geometry = PlacementGeometry((_array("s", Position(0, 0)),), (_gates(),))
    with pytest.raises(KeyError):
        geometry.locate("absent")
    with pytest.raises(KeyError):
        geometry.site_location(Position(500, 500))
