"""Rigid AOD motion, transfer, and Device snapshot contracts."""

import json
from dataclasses import replace
from typing import Literal

import pytest

from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    loads_device,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.state import (
    COORDINATE_TOLERANCE,
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)


def _device(
    *sites: SLMSite,
    motion: Literal["flexible", "rigid"] = "rigid",
    max_rows: int | None = None,
) -> Device:
    return Device(
        "motion-test",
        (),
        sites,
        (
            AOD(
                "transport",
                Region(0, 0, 100, 100),
                min_spacing=1,
                max_rows=max_rows,
                motion=motion,
            ),
        ),
        Timing(load_us=1, store_us=2, move_us_per_um=3),
    )


def _grid(
    rows: tuple[tuple[int, float], ...] = ((4, 10), (9, 20)),
    columns: tuple[tuple[int, float], ...] = ((3, 10), (8, 20)),
) -> AODGrid:
    return AODGrid(
        "transport",
        tuple(AODLine(identifier, coordinate) for identifier, coordinate in rows),
        tuple(AODLine(identifier, coordinate) for identifier, coordinate in columns),
    )


def _carried(atom_id: int, grid: AODGrid, row: int, column: int) -> AtomState:
    return AtomState(
        atom_id,
        grid.position(row, column),
        AODHolder(grid.aod_id, row, column),
    )


def _stored(atom_id: int, site: SLMSite) -> AtomState:
    return AtomState(atom_id, site.position, SLMHolder(site.id))


@pytest.mark.parametrize("motion", ["flexible", "rigid"])
def test_device_motion_round_trips_through_json_snapshot(
    motion: Literal["flexible", "rigid"],
) -> None:
    device = _device(motion=motion)
    document = encode_device(device)

    assert document["aods"][0]["motion"] == motion
    assert loads_device(json.dumps(document)) == device


def test_missing_device_motion_keeps_existing_flexible_default() -> None:
    device = _device(motion="flexible")
    document = encode_device(device)
    document["aods"][0].pop("motion")

    assert AOD("a", Region(0, 0, 1, 1), 1).motion == "flexible"
    assert loads_device(json.dumps(document)) == device


@pytest.mark.parametrize("motion", ["rigid_translation", "", None, 1])
def test_device_rejects_unknown_motion_values(motion: object) -> None:
    document = encode_device(_device())
    document["aods"][0]["motion"] = motion

    with pytest.raises(ValueError, match="motion"):
        loads_device(json.dumps(document))


@pytest.mark.parametrize("dx,dy", [(0, 0), (30, 20), (-5, -8), (0, 20), (30, 0)])
def test_rigid_move_translates_all_lines_by_one_vector(dx: float, dy: float) -> None:
    source = _grid()
    target = _grid(((4, 10 + dy), (9, 20 + dy)), ((3, 10 + dx), (8, 20 + dx)))
    layout = AtomLayout(
        (_carried(7, source, 4, 3), _carried(11, source, 9, 8)), (source,)
    )
    device = _device()
    validate_layout(device, layout)

    checked = check_move(
        device, layout, target, {7: target.position(4, 3), 11: target.position(9, 8)}
    )

    assert checked.ok
    assert layout.grid("transport") == source
    assert layout.atom(7).position == Position(10, 10)


@pytest.mark.parametrize(
    "target",
    [
        _grid(((4, 30), (9, 45)), ((3, 40), (8, 50))),
        _grid(((4, 30), (9, 40)), ((3, 40), (8, 55))),
    ],
)
def test_rigid_rejects_deformation_that_flexible_accepts(target: AODGrid) -> None:
    source = _grid()
    layout = AtomLayout(
        (_carried(7, source, 4, 3), _carried(11, source, 9, 8)), (source,)
    )
    targets = {7: target.position(4, 3), 11: target.position(9, 8)}

    assert check_move(_device(), layout, target, targets).rule == "rigid_translation"
    assert check_move(_device(motion="flexible"), layout, target, targets).ok


@pytest.mark.parametrize(
    "target",
    [
        _grid(((4, 30), (9, 45)), ((3, 40), (8, 50))),
        _grid(((4, 30), (9, 40)), ((3, 40), (8, 55))),
    ],
)
def test_rigid_translation_includes_lines_with_no_carried_atoms(
    target: AODGrid,
) -> None:
    source = _grid()
    layout = AtomLayout((_carried(7, source, 4, 3),), (source,))

    checked = check_move(_device(), layout, target, {7: target.position(4, 3)})

    assert checked.rule == "rigid_translation"


@pytest.mark.parametrize("axis", ["rows", "columns"])
@pytest.mark.parametrize("offset,accepted", [(0.5, True), (2.0, False)])
def test_rigid_translation_uses_absolute_coordinate_tolerance(
    axis: str, offset: float, accepted: bool
) -> None:
    source = _grid()
    drift = offset * COORDINATE_TOLERANCE
    target = _grid(
        ((4, 30), (9, 40 + (drift if axis == "rows" else 0))),
        ((3, 40), (8, 50 + (drift if axis == "columns" else 0))),
    )
    layout = AtomLayout(
        (_carried(7, source, 4, 3), _carried(11, source, 9, 8)), (source,)
    )

    checked = check_move(
        _device(), layout, target, {7: target.position(4, 3), 11: target.position(9, 8)}
    )

    assert checked.ok is accepted
    assert checked.rule == (None if accepted else "rigid_translation")


def test_rigid_motion_matches_line_ids_independently_of_tuple_order() -> None:
    source = _grid(((90, 10), (2, 20)), ((80, 10), (1, 20)))
    target = _grid(((2, 40), (90, 30)), ((1, 50), (80, 40)))
    layout = AtomLayout((_carried(7, source, 90, 80),), (source,))

    assert check_move(_device(), layout, target, {7: Position(40, 30)}).ok


@pytest.mark.parametrize(
    "target,rule",
    [
        (_grid(((4, 30), (9, 110)), ((3, 40), (8, 50))), "invalid_grid"),
        (_grid(((4, 30), (9, 30.5)), ((3, 40), (8, 50))), "invalid_grid"),
        (_grid(((4, 30),), ((3, 40), (8, 50))), "line_identity"),
        (_grid(((4, 40), (9, 30)), ((3, 40), (8, 50))), "order"),
    ],
)
def test_rigid_motion_preserves_existing_grid_and_identity_checks(
    target: AODGrid, rule: str
) -> None:
    source = _grid()
    layout = AtomLayout((_carried(7, source, 4, 3),), (source,))

    assert (
        check_move(_device(), layout, target, {7: target.position(4, 3)}).rule == rule
    )


def test_rigid_motion_keeps_all_carried_atoms_explicit() -> None:
    source = _grid()
    target = _grid(((4, 30), (9, 40)), ((3, 40), (8, 50)))
    layout = AtomLayout(
        (_carried(7, source, 4, 3), _carried(11, source, 9, 8)), (source,)
    )

    assert (
        check_move(_device(), layout, target, {7: target.position(4, 3)}).rule
        == "omitted_atom"
    )
    assert (
        check_move(
            _device(), layout, target, {7: Position(41, 30), 11: target.position(9, 8)}
        ).rule
        == "target_mismatch"
    )


def test_rigid_motion_still_checks_cross_intersection_ghosts() -> None:
    source = _grid()
    target = _grid(((4, 30), (9, 40)), ((3, 40), (8, 50)))
    spectator = SLMSite("spectator", Position(50, 30))
    layout = AtomLayout(
        (
            _carried(7, source, 4, 3),
            _carried(11, source, 9, 8),
            _stored(13, spectator),
        ),
        (source,),
    )
    device = _device(spectator)
    validate_layout(device, layout)

    checked = check_move(
        device, layout, target, {7: target.position(4, 3), 11: target.position(9, 8)}
    )

    assert checked.rule == "ghost_pickup"


def test_rigid_initial_load_can_activate_multiple_rows_and_columns() -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    layout = AtomLayout((_stored(7, first), _stored(11, second)))

    assert check_load(
        _device(first, second), layout, _grid(), {7: (4, 3), 11: (9, 8)}
    ).ok


@pytest.mark.parametrize(
    "target,position,binding",
    [
        (_grid(((4, 10), (9, 20)), ((3, 10),)), Position(10, 20), (9, 3)),
        (_grid(((4, 10),), ((3, 10), (8, 20))), Position(20, 10), (4, 8)),
    ],
)
def test_loaded_rigid_aod_cannot_add_rows_or_columns(
    target: AODGrid, position: Position, binding: tuple[int, int]
) -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("incoming", position)
    layout = AtomLayout((_carried(7, source, 4, 3), _stored(11, site)), (source,))

    assert (
        check_load(_device(site), layout, target, {11: binding}).rule == "rigid_loading"
    )
    assert check_load(
        _device(site, motion="flexible"), layout, target, {11: binding}
    ).ok


def test_rigid_can_expand_preactivated_grid_when_it_carries_no_atoms() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("incoming", Position(20, 20))
    layout = AtomLayout((_stored(11, site),), (source,))
    device = _device(site)
    validate_layout(device, layout)

    assert check_load(device, layout, _grid(), {11: (9, 8)}).ok


def test_rigid_line_addition_only_depends_on_its_own_carried_atoms() -> None:
    incoming = SLMSite("incoming", Position(20, 20))
    other_grid = AODGrid("other", (AODLine(0, 70),), (AODLine(0, 70),))
    layout = AtomLayout(
        (_stored(11, incoming), _carried(7, other_grid, 0, 0)), (other_grid,)
    )
    device = _device(incoming)
    device = replace(
        device,
        aods=(*device.aods, replace(device.aods[0], id="other")),
    )
    validate_layout(device, layout)

    assert check_load(device, layout, _grid(), {11: (9, 8)}).ok


def test_rigid_noop_transfer_preserves_loaded_lines() -> None:
    grid = _grid()
    layout = AtomLayout((_carried(7, grid, 4, 3),), (grid,))
    device = _device()

    assert check_load(device, layout, grid, {}).ok
    assert check_store(device, layout, grid, {}).ok
    assert check_move(device, layout, grid, {}).ok


def test_rigid_load_preserves_line_motion_and_capacity_checks() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 15), (9, 20)), ((3, 10),))
    site = SLMSite("incoming", Position(10, 20))
    layout = AtomLayout((_carried(7, source, 4, 3), _stored(11, site)), (source,))

    assert check_load(_device(site), layout, target, {11: (9, 3)}).rule == "line_motion"
    assert (
        check_load(_device(site, max_rows=1), layout, target, {11: (9, 3)}).rule
        == "invalid_grid"
    )


def test_rigid_store_can_remove_lines_without_moving_remaining_atoms() -> None:
    source = _grid()
    remaining = _grid(((9, 20),), ((3, 10), (8, 20)))
    destination = SLMSite("destination", Position(10, 10))
    layout = AtomLayout(
        (_carried(7, source, 4, 3), _carried(11, source, 9, 8)), (source,)
    )

    assert check_store(_device(destination), layout, remaining, {7: destination.id}).ok


def test_rigid_load_preserves_cross_intersection_ghost_checks() -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    spectator = SLMSite("spectator", Position(20, 10))
    layout = AtomLayout(
        (_stored(7, first), _stored(11, second), _stored(13, spectator))
    )

    checked = check_load(
        _device(first, second, spectator), layout, _grid(), {7: (4, 3), 11: (9, 8)}
    )

    assert checked.rule == "ghost_pickup"


def test_empty_rigid_grid_accepts_empty_move() -> None:
    assert check_move(_device(), AtomLayout(()), AODGrid("transport"), {}).ok
