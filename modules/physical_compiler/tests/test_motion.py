"""Tests for collective AOD movement and line-activation transfers."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

import pytest

from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
)
from nadit_physical_compiler.motion import (
    ConstraintResult,
    check_load,
    check_move,
    check_store,
)
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)


def _grid(
    rows: tuple[tuple[int, float], ...],
    columns: tuple[tuple[int, float], ...],
) -> AODGrid:
    return AODGrid(
        "transport",
        tuple(AODLine(line_id, coordinate) for line_id, coordinate in rows),
        tuple(AODLine(line_id, coordinate) for line_id, coordinate in columns),
    )


def _device(
    *sites: SLMSite,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> Device:
    return Device(
        name="reference",
        zones=(),
        slm_sites=sites,
        aods=(
            AOD(
                "transport",
                Region(0, 0, 100, 100),
                min_spacing=1,
                max_rows=max_rows,
                max_columns=max_columns,
            ),
        ),
        timing=Timing(load_us=1, store_us=1, move_us_per_um=1),
    )


def _carried(atom_id: int, grid: AODGrid, row: int, column: int) -> AtomState:
    return AtomState(
        atom_id,
        grid.position(row, column),
        AODHolder(grid.aod_id, row, column),
    )


def _stored(atom_id: int, site: SLMSite) -> AtomState:
    return AtomState(atom_id, site.position, SLMHolder(site.id))


def test_constraint_result_distinguishes_success_from_failed_rule() -> None:
    assert ConstraintResult().ok
    failure = ConstraintResult("order", "Rows cannot exchange their spatial order.")
    assert not failure.ok
    assert failure.rule == "order"
    assert failure.message == "Rows cannot exchange their spatial order."


def test_move_allows_row_and_column_spacing_to_change_together() -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))
    target = _grid(((4, 30), (9, 45)), ((3, 40), (8, 65)))
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 9, 8)), (source,)
    )

    result = check_move(
        _device(), layout, target, {0: Position(40, 30), 1: Position(65, 45)}
    )

    assert result.ok
    assert layout.grid("transport") == source
    assert layout.atom(0).position == Position(10, 10)


def test_move_preserves_spatial_order_instead_of_numeric_line_id_order() -> None:
    source = _grid(((90, 10), (2, 20)), ((80, 10), (1, 20)))
    target = _grid(((90, 30), (2, 45)), ((80, 40), (1, 65)))
    layout = AtomLayout((_carried(0, source, 90, 80),), (source,))

    assert check_move(_device(), layout, target, {0: Position(40, 30)}).ok


@pytest.mark.parametrize(
    "target",
    [
        _grid(((4, 20), (9, 10)), ((3, 10), (8, 20))),
        _grid(((4, 10), (9, 20)), ((3, 20), (8, 10))),
    ],
)
def test_move_rejects_row_or_column_crossing(target: AODGrid) -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    result = check_move(_device(), layout, target, {0: target.position(4, 3)})

    assert not result.ok
    assert result.rule == "order"


@pytest.mark.parametrize(
    "target",
    [
        _grid(((4, 10), (9, 20)), ((3, 10),)),
        _grid(((4, 10),), ((3, 10),)),
        _grid(((4, 10), (5, 20)), ((3, 10), (8, 20))),
    ],
)
def test_move_cannot_remove_or_replace_active_lines(target: AODGrid) -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    result = check_move(_device(), layout, target, {})

    assert not result.ok
    assert result.rule == "line_identity"


@pytest.mark.parametrize(
    "target",
    [
        _grid(((4, 10), (9, 10.5)), ((3, 10),)),
        _grid(((4, 10), (9, 101)), ((3, 10),)),
    ],
)
def test_move_checks_spacing_and_workspace_for_empty_lines(target: AODGrid) -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10),))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    assert not check_move(_device(), layout, target, {}).ok


def test_move_rejects_an_omitted_atom_carried_by_the_moving_row() -> None:
    source = _grid(((4, 10),), ((3, 10), (8, 20)))
    target = _grid(((4, 30),), ((3, 10), (8, 20)))
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 4, 8)), (source,)
    )

    result = check_move(_device(), layout, target, {0: Position(10, 30)})

    assert not result.ok
    assert result.rule == "omitted_atom"
    assert layout.atom(1).position == Position(20, 10)


def test_move_rejects_independent_target_incompatible_with_shared_grid() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 30),), ((3, 40),))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    result = check_move(_device(), layout, target, {0: Position(41, 30)})

    assert not result.ok
    assert result.rule == "target_mismatch"


def test_moving_an_empty_line_can_create_an_unwanted_slm_intersection() -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10),))
    target = _grid(((4, 10), (9, 30)), ((3, 10),))
    site = SLMSite("spectator", Position(10, 30))
    layout = AtomLayout((_carried(0, source, 4, 3), _stored(1, site)), (source,))

    result = check_move(_device(site), layout, target, {})

    assert not result.ok
    assert result.rule == "ghost_pickup"


def test_move_rejects_a_destination_occupied_by_another_aod() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 30),), ((3, 30),))
    other_grid = AODGrid("secondary", (AODLine(0, 30),), (AODLine(0, 30),))
    device = _device()
    device = replace(
        device,
        aods=(*device.aods, AOD("secondary", Region(0, 0, 100, 100), min_spacing=1)),
    )
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, other_grid, 0, 0)),
        (source, other_grid),
    )

    result = check_move(device, layout, target, {0: Position(30, 30)})

    assert not result.ok
    assert result.rule == "occupied"


def test_load_all_atoms_at_a_rectangular_set_of_intersections() -> None:
    target = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))
    sites = (
        SLMSite("a", Position(10, 10)),
        SLMSite("b", Position(20, 10)),
        SLMSite("c", Position(10, 20)),
        SLMSite("d", Position(20, 20)),
    )
    layout = AtomLayout(tuple(_stored(index, site) for index, site in enumerate(sites)))
    loads = {0: (4, 3), 1: (4, 8), 2: (9, 3), 3: (9, 8)}

    result = check_load(_device(*sites), layout, target, loads)

    assert result.ok
    assert layout.aod_grids == ()
    assert isinstance(layout.atom(0).holder, SLMHolder)


def test_load_can_add_a_row_without_moving_existing_lines() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 10), (9, 20)), ((3, 10),))
    site = SLMSite("incoming", Position(10, 20))
    layout = AtomLayout((_carried(0, source, 4, 3), _stored(1, site)), (source,))

    assert check_load(_device(site), layout, target, {1: (9, 3)}).ok


def test_load_cannot_reposition_an_existing_line() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 15), (9, 20)), ((3, 10),))
    site = SLMSite("incoming", Position(10, 20))
    layout = AtomLayout((_carried(0, source, 4, 3), _stored(1, site)), (source,))

    result = check_load(_device(site), layout, target, {1: (9, 3)})

    assert not result.ok
    assert result.rule == "line_motion"


def test_load_cannot_remove_an_existing_empty_line() -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10),))
    target = _grid(((4, 10),), ((3, 10),))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))
    device = _device()
    validate_layout(device, layout)
    snapshot = deepcopy(layout)

    result = check_load(device, layout, target, {})

    assert not result.ok
    assert result.rule == "line_identity"
    assert layout == snapshot


@pytest.mark.parametrize(
    ("atom_id", "rule"), [(999, "unknown_atom"), (0, "wrong_holder")]
)
def test_load_requires_an_existing_slm_held_atom(atom_id: int, rule: str) -> None:
    grid = _grid(((4, 10),), ((3, 10),))
    layout = AtomLayout((_carried(0, grid, 4, 3),), (grid,))
    device = _device()
    validate_layout(device, layout)
    snapshot = deepcopy(layout)
    loads = {atom_id: (4, 3)}

    result = check_load(device, layout, grid, loads)

    assert not result.ok
    assert result.rule == rule
    assert layout == snapshot
    assert loads == {atom_id: (4, 3)}


def test_load_requires_atom_to_be_at_the_selected_intersection() -> None:
    site = SLMSite("source", Position(10, 10))
    layout = AtomLayout((_stored(0, site),))
    target = _grid(((4, 10),), ((3, 20),))

    result = check_load(_device(site), layout, target, {0: (4, 3)})

    assert not result.ok
    assert result.rule == "alignment"


def test_simultaneous_row_and_column_loading_exposes_cross_intersections() -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    spectator = SLMSite("spectator", Position(20, 10))
    device = _device(first, second, spectator)
    layout = AtomLayout((_stored(0, first), _stored(1, second), _stored(2, spectator)))

    assert check_load(device, layout, _grid(((4, 10),), ((3, 10),)), {0: (4, 3)}).ok
    assert check_load(device, layout, _grid(((9, 20),), ((8, 20),)), {1: (9, 8)}).ok
    result = check_load(
        device,
        layout,
        _grid(((4, 10), (9, 20)), ((3, 10), (8, 20))),
        {0: (4, 3), 1: (9, 8)},
    )

    assert not result.ok
    assert result.rule == "ghost_pickup"


def test_row_loading_with_an_intermediate_offset_avoids_cross_pickup() -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    spectator = SLMSite("spectator", Position(20, 10))
    device = _device(first, second, spectator)
    initial = AtomLayout((_stored(0, first), _stored(1, second), _stored(2, spectator)))
    loaded_grid = _grid(((4, 10),), ((3, 10),))
    assert check_load(device, initial, loaded_grid, {0: (4, 3)}).ok

    loaded = AtomLayout(
        (_carried(0, loaded_grid, 4, 3), _stored(1, second), _stored(2, spectator)),
        (loaded_grid,),
    )
    offset_grid = _grid(((4, 5),), ((3, 10),))
    assert check_move(device, loaded, offset_grid, {0: Position(10, 5)}).ok

    offset = AtomLayout(
        (_carried(0, offset_grid, 4, 3), _stored(1, second), _stored(2, spectator)),
        (offset_grid,),
    )
    final_grid = _grid(((4, 5), (9, 20)), ((3, 10), (8, 20)))
    assert check_load(device, offset, final_grid, {1: (9, 8)}).ok


@pytest.mark.parametrize(
    ("max_rows", "max_columns", "expected"),
    [(None, None, True), (1, None, False), (None, 1, False), (2, 2, True)],
)
def test_loading_enforces_finite_or_unlimited_capacity(
    max_rows: int | None, max_columns: int | None, expected: bool
) -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    layout = AtomLayout((_stored(0, first), _stored(1, second)))
    target = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))

    result = check_load(
        _device(first, second, max_rows=max_rows, max_columns=max_columns),
        layout,
        target,
        {0: (4, 3), 1: (9, 8)},
    )

    assert result.ok is expected


def test_store_can_deactivate_a_row_while_other_rows_remain_loaded() -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10),))
    target = _grid(((9, 20),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 9, 3)), (source,)
    )

    result = check_store(_device(site), layout, target, {0: "destination"})

    assert result.ok
    assert layout.grid("transport") == source
    assert isinstance(layout.atom(0).holder, AODHolder)


def test_store_can_deactivate_the_entire_grid() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    assert check_store(_device(site), layout, _grid((), ()), {0: "destination"}).ok


def test_store_requires_the_selected_trap_to_be_deactivated() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    result = check_store(_device(site), layout, source, {0: "destination"})

    assert not result.ok
    assert result.rule == "active_trap"


def test_store_cannot_release_an_unlisted_atom_sharing_the_removed_row() -> None:
    source = _grid(((4, 10),), ((3, 10), (8, 20)))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 4, 8)), (source,)
    )

    result = check_store(
        _device(site),
        layout,
        _grid((), ((3, 10), (8, 20))),
        {0: "destination"},
    )

    assert not result.ok
    assert result.rule == "unintended_release"


def test_store_requires_an_aligned_slm_site() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("destination", Position(20, 10))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))

    result = check_store(_device(site), layout, _grid((), ()), {0: "destination"})

    assert not result.ok
    assert result.rule == "alignment"


def test_store_cannot_overwrite_an_occupied_slm_site() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout((_carried(0, source, 4, 3), _stored(1, site)), (source,))

    result = check_store(_device(site), layout, _grid((), ()), {0: "destination"})

    assert not result.ok
    assert result.rule == "occupied"


def test_store_cannot_move_the_lines_that_remain_active() -> None:
    source = _grid(((4, 10), (9, 20)), ((3, 10),))
    target = _grid(((9, 30),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    layout = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 9, 3)), (source,)
    )

    result = check_store(_device(site), layout, target, {0: "destination"})

    assert not result.ok
    assert result.rule == "line_motion"


def test_store_cannot_activate_new_lines() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    target = _grid(((4, 10), (9, 20)), ((3, 10),))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))
    device = _device()
    validate_layout(device, layout)
    snapshot = deepcopy(layout)

    result = check_store(device, layout, target, {})

    assert not result.ok
    assert result.rule == "line_identity"
    assert layout == snapshot


def test_store_requires_a_known_slm_destination() -> None:
    source = _grid(((4, 10),), ((3, 10),))
    layout = AtomLayout((_carried(0, source, 4, 3),), (source,))
    device = _device()
    validate_layout(device, layout)
    snapshot = deepcopy(layout)
    stores = {0: "missing"}

    result = check_store(device, layout, _grid((), ()), stores)

    assert not result.ok
    assert result.rule == "alignment"
    assert layout == snapshot
    assert stores == {0: "missing"}


def test_store_cannot_leave_atom_on_another_aods_active_intersection() -> None:
    source = AODGrid("first", (AODLine(4, 10),), (AODLine(3, 10),))
    other = AODGrid("second", (AODLine(9, 10),), (AODLine(8, 10),))
    site = SLMSite("receive", Position(10, 10))
    device = replace(
        _device(site),
        aods=(
            AOD("first", Region(0, 0, 100, 100), min_spacing=1),
            AOD("second", Region(0, 0, 100, 100), min_spacing=1),
        ),
    )
    layout = AtomLayout((_carried(0, source, 4, 3),), (source, other))
    validate_layout(device, layout)

    result = check_store(device, layout, AODGrid("first"), {0: "receive"})

    assert not result.ok
    assert result.rule == "ghost_pickup"


def test_checks_accept_noops_without_changing_the_input_state() -> None:
    grid = _grid(((4, 10),), ((3, 10),))
    atom = _carried(0, grid, 4, 3)
    layout = AtomLayout((atom,), (grid,))
    device = _device()

    assert check_move(device, layout, grid, {}).ok
    assert check_load(device, layout, grid, {}).ok
    assert check_store(device, layout, grid, {}).ok
    assert layout.atoms == (atom,)
    assert layout.aod_grids == (grid,)


@pytest.mark.parametrize(
    "targets",
    [{999: Position(10, 10)}, {1: Position(20, 20)}],
)
def test_move_requires_targets_to_reference_atoms_carried_by_this_aod(
    targets: Mapping[int, Position],
) -> None:
    grid = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("stored", Position(20, 20))
    layout = AtomLayout((_carried(0, grid, 4, 3), _stored(1, site)), (grid,))

    assert not check_move(_device(site), layout, grid, targets).ok
