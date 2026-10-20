"""Active-grid occupancy uses the same coordinate semantics at every density."""

import pytest

from nadit_physical_compiler.device import Position
from nadit_physical_compiler.state import COORDINATE_TOLERANCE
from nadit_physical_compiler.transport.occupancy import Occupancy


def _axes(width: int) -> tuple[float, ...]:
    return tuple(float(10 * index) for index in range(width))


def _occupied() -> Occupancy:
    return Occupancy(
        {
            0: Position(0, 0),
            1: Position(10, 10),
            2: Position(0, 10),
            3: Position(5, 5),
            4: Position(15, 5),
            5: Position(5, 15),
        }
    )


@pytest.mark.parametrize("width", [2, 64], ids=["dense", "sparse"])
def test_active_grid_finds_only_occupied_intersections(width: int) -> None:
    occupied = _occupied()
    axis = _axes(width)

    assert occupied.at_grid(axis, axis) == {0, 1, 2}


@pytest.mark.parametrize("width", [2, 64], ids=["dense", "sparse"])
def test_active_grid_uses_coordinate_tolerance_on_both_axes(width: int) -> None:
    tolerance = COORDINATE_TOLERANCE
    occupied = Occupancy(
        {
            0: Position(tolerance / 2, -tolerance / 2),
            1: Position(10 + tolerance / 2, 10 + tolerance / 2),
            2: Position(10 + 2 * tolerance, 0),
            3: Position(0, 10 + 2 * tolerance),
            4: Position(5, 5),
            5: Position(15, 5),
        }
    )
    axis = _axes(width)

    assert occupied.at_grid(axis, axis) == {0, 1}


@pytest.mark.parametrize("width", [2, 64], ids=["dense", "sparse"])
def test_active_grid_sees_current_positions_after_remove_and_add(width: int) -> None:
    occupied = _occupied()
    axis = _axes(width)
    occupied.remove(1)
    occupied.add(7, Position(10, 10))
    occupied.remove(0)
    occupied.add(0, Position(5, 25))

    assert occupied.at_grid(axis, axis) == {2, 7}

    occupied.add(1, Position(0, 0))
    assert occupied.at_grid(axis, axis) == {1, 2, 7}


def test_active_grid_with_an_empty_axis_or_no_atoms_has_no_occupants() -> None:
    occupied = _occupied()

    assert occupied.at_grid((), (0.0, 10.0)) == set()
    assert occupied.at_grid((0.0, 10.0), ()) == set()
    assert Occupancy({}).at_grid(_axes(64), _axes(64)) == set()
