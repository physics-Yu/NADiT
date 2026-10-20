"""Automatic source row order and complete multi-SLM initialization."""

import pytest

from nadit_physical_compiler.device import Position, slm_grid
from nadit_physical_compiler.placement.geometry import PlacementGeometry, SLMArray
from nadit_physical_compiler.placement.initial import make_initial_layout
from nadit_physical_compiler.state import SLMHolder


def _geometry(gate_y: float, storage_count: int = 1) -> PlacementGeometry:
    arrays: list[SLMArray] = []
    for index in range(storage_count):
        sites = slm_grid(f"s{index}", Position(20 * index, 0), 3, 2, Position(5, 5))
        arrays.append(
            SLMArray(
                f"s{index}",
                "storage",
                (sites[:2], sites[2:4], sites[4:]),
                Position(5, 5),
            )
        )
    left = (slm_grid("l", Position(0, gate_y), 1, 2, Position(8, 5)),)
    right = (slm_grid("r", Position(2, gate_y), 1, 2, Position(8, 5)),)
    gates = (
        SLMArray("l", "left", left, Position(8, 5), right),
        SLMArray("r", "right", right, Position(8, 5), left),
    )
    return PlacementGeometry(tuple(arrays), (gates,))


@pytest.mark.parametrize(
    ("gate_y", "expected"),
    (
        (100, ("s0_r2_c0", "s0_r2_c1", "s0_r1_c0", "s0_r1_c1")),
        (-100, ("s0_r0_c0", "s0_r0_c1", "s0_r1_c0", "s0_r1_c1")),
        (0, ("s0_r0_c0", "s0_r0_c1", "s0_r1_c0", "s0_r1_c1")),
    ),
)
def test_initial_rows_start_on_source_selected_side(
    gate_y: float, expected: tuple[str, ...]
) -> None:
    geometry = _geometry(gate_y)
    layout = make_initial_layout(4, geometry)
    assert tuple(atom.holder for atom in layout.atoms) == tuple(
        SLMHolder(site) for site in expected
    )
    assert layout.qubit_to_atom == (0, 1, 2, 3)
    assert layout.aod_grids == ()


@pytest.mark.parametrize("gate_y", (-100, 100))
def test_cross_slm_fill_restarts_same_direction_and_handles_exact_capacity(
    gate_y: float,
) -> None:
    geometry = _geometry(gate_y, storage_count=2)
    layout = make_initial_layout(12, geometry)
    rows = (2, 1, 0) if gate_y > 0 else (0, 1, 2)
    expected = tuple(
        f"s{array}_r{row}_c{column}"
        for array in range(2)
        for row in rows
        for column in range(2)
    )
    assert tuple(atom.holder for atom in layout.atoms) == tuple(
        SLMHolder(site) for site in expected
    )
    assert len({atom.position for atom in layout.atoms}) == 12


def test_initial_layout_preserves_supplied_atom_identities() -> None:
    layout = make_initial_layout(3, _geometry(100), atom_ids=(17, 41, 8))
    assert tuple(atom.id for atom in layout.atoms) == (17, 41, 8)
    assert layout.qubit_to_atom == (17, 41, 8)


def test_zero_qubits_needs_no_arrays() -> None:
    layout = make_initial_layout(0, PlacementGeometry((), ()))
    assert layout.atoms == ()
    assert layout.qubit_to_atom == ()


@pytest.mark.parametrize(
    ("count", "ids", "message"),
    (
        (-1, None, "nonnegative"),
        (2, (5,), "match"),
        (7, None, "7 storage sites; 6 available"),
    ),
)
def test_initial_layout_reports_input_capacity_and_identity_errors(
    count: int, ids: tuple[int, ...] | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        make_initial_layout(count, _geometry(100), atom_ids=ids)
