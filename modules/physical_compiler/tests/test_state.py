"""Atom ownership and device-bound layout validation."""

from dataclasses import FrozenInstanceError

import pytest

from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
)
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_grid,
    validate_layout,
)


def test_line_identity_is_independent_of_spatial_order() -> None:
    grid = AODGrid(
        "aod",
        rows=(AODLine(8, 4), AODLine(2, 9)),
        columns=(AODLine(6, 3), AODLine(1, 7)),
    )

    assert grid.position(2, 6) == Position(3, 9)
    assert grid.rows[0].id == 8
    with pytest.raises(KeyError, match="row"):
        grid.position(0, 6)
    with pytest.raises(KeyError, match="column"):
        grid.position(2, 0)


def test_layout_preserves_unoccupied_lines_and_sparse_atom_ids() -> None:
    grid = AODGrid(
        "aod",
        rows=(AODLine(3, 4), AODLine(9, 8)),
        columns=(AODLine(7, 2), AODLine(1, 6)),
    )
    atom = AtomState(42, Position(2, 4), AODHolder("aod", 3, 7))
    spare = AtomState(105, Position(0, 0), SLMHolder("home"))
    layout = AtomLayout((atom, spare), (grid,), (42,))

    assert layout.atom(42) is atom
    assert layout.grid("aod") is grid
    assert layout.grid("unused") == AODGrid("unused")
    assert layout.qubit_to_atom == (42,)
    assert len(layout.grid("aod").rows) == 2
    with pytest.raises(KeyError, match="111"):
        layout.atom(111)
    with pytest.raises(FrozenInstanceError):
        atom.id = 7  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AODLine(-1, 0),
        lambda: AODLine(0, float("inf")),
        lambda: AODLine(0, float("nan")),
        lambda: AODGrid("", (), ()),
        lambda: AODGrid("aod", (AODLine(0, 1), AODLine(0, 2))),
        lambda: AODGrid("aod", (), (AODLine(0, 1), AODLine(0, 2))),
        lambda: SLMHolder(""),
        lambda: AODHolder("", 0, 0),
        lambda: AODHolder("aod", -1, 0),
        lambda: AODHolder("aod", 0, -1),
        lambda: AtomState(-1, Position(0, 0), SLMHolder("s")),
    ],
)
def test_invalid_record_structure_is_rejected(factory: object) -> None:
    assert callable(factory)
    with pytest.raises(ValueError):
        factory()


def test_layout_rejects_duplicate_ids_and_invalid_qubit_binding() -> None:
    atom = AtomState(4, Position(0, 0), SLMHolder("s"))
    with pytest.raises(ValueError, match="atom"):
        AtomLayout((atom, atom))
    with pytest.raises(ValueError, match="AOD"):
        AtomLayout((), (AODGrid("aod"), AODGrid("aod")))
    with pytest.raises(ValueError, match="mapping"):
        AtomLayout((atom,), qubit_to_atom=(5,))
    with pytest.raises(ValueError, match="mapping"):
        AtomLayout((atom,), qubit_to_atom=(4, 4))


def test_empty_layout_is_valid_without_bindings() -> None:
    assert AtomLayout(()).qubit_to_atom == ()


@pytest.fixture
def device() -> Device:
    return Device(
        "test",
        (),
        (SLMSite("home", Position(0, 0)), SLMSite("gate", Position(2, 4))),
        (
            AOD("aod", Region(-10, -10, 20, 20), 2, 2, 3),
            AOD("other", Region(-10, -10, 20, 20), 2),
        ),
        Timing(1, 1, 1),
    )


def test_validate_layout_preserves_holder_independently_of_sites(
    device: Device,
) -> None:
    grid = AODGrid("aod", (AODLine(9, 4),), (AODLine(7, 2), AODLine(1, 6)))
    layout = AtomLayout(
        (
            AtomState(10, Position(0, 0), SLMHolder("home")),
            AtomState(20, Position(2, 4), AODHolder("aod", 9, 7)),
        ),
        (grid,),
        (20, 10),
    )

    validate_layout(device, layout)
    assert layout.atom(20).holder == AODHolder("aod", 9, 7)


@pytest.mark.parametrize(
    ("grid", "message"),
    [
        (AODGrid("missing"), "Unknown AOD"),
        (
            AODGrid("aod", tuple(AODLine(i, i * 2) for i in range(3))),
            "row capacity",
        ),
        (
            AODGrid("aod", (), tuple(AODLine(i, i * 2) for i in range(4))),
            "column capacity",
        ),
        (AODGrid("aod", (AODLine(0, -11),)), "workspace"),
        (AODGrid("aod", (), (AODLine(0, 21),)), "workspace"),
        (AODGrid("aod", (AODLine(0, 0), AODLine(1, 1))), "spacing"),
        (AODGrid("aod", (), (AODLine(0, 0), AODLine(1, 1))), "spacing"),
    ],
)
def test_validate_grid_rejects_device_violations(
    device: Device, grid: AODGrid, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_grid(device, grid)


def test_validate_grid_uses_geometric_order_and_includes_vacant_lines(
    device: Device,
) -> None:
    grid = AODGrid(
        "aod",
        (AODLine(8, 20), AODLine(2, -10)),
        (AODLine(5, 20), AODLine(1, -10), AODLine(9, -8)),
    )
    validate_grid(device, grid)
    validate_layout(device, AtomLayout((), (grid,)))
    validate_grid(device, AODGrid("aod", (AODLine(0, 20 + 1e-10),)))


@pytest.mark.parametrize(
    ("atom", "message"),
    [
        (AtomState(1, Position(0, 0), SLMHolder("missing")), "SLM"),
        (AtomState(1, Position(1, 0), SLMHolder("home")), "position"),
        (AtomState(1, Position(2, 4), AODHolder("missing", 0, 0)), "AOD"),
        (AtomState(1, Position(2, 4), AODHolder("aod", 99, 0)), "row"),
        (AtomState(1, Position(2, 4), AODHolder("aod", 0, 99)), "column"),
        (AtomState(1, Position(3, 4), AODHolder("aod", 0, 0)), "position"),
    ],
)
def test_validate_layout_rejects_invalid_initial_binding(
    device: Device, atom: AtomState, message: str
) -> None:
    grid = AODGrid("aod", (AODLine(0, 4),), (AODLine(0, 2),))
    with pytest.raises(ValueError, match=message):
        validate_layout(device, AtomLayout((atom,), (grid,)))


def test_validate_layout_checks_holder_and_physical_occupancy(device: Device) -> None:
    with pytest.raises(ValueError, match="occupied"):
        validate_layout(
            device,
            AtomLayout(
                (
                    AtomState(0, Position(0, 0), SLMHolder("home")),
                    AtomState(1, Position(0, 0), SLMHolder("home")),
                )
            ),
        )

    grids = (
        AODGrid("aod", (AODLine(0, 4),), (AODLine(0, 2),)),
        AODGrid("other", (AODLine(0, 4),), (AODLine(0, 2),)),
    )
    with pytest.raises(ValueError, match="position"):
        validate_layout(
            device,
            AtomLayout(
                (
                    AtomState(0, Position(2, 4), AODHolder("aod", 0, 0)),
                    AtomState(1, Position(2, 4), AODHolder("other", 0, 0)),
                ),
                grids,
            ),
        )


def test_validate_layout_rejects_ghost_intersections_with_slm_atoms(
    device: Device,
) -> None:
    atom = AtomState(0, Position(0, 0), SLMHolder("home"))
    grid = AODGrid("aod", (AODLine(0, 0),), (AODLine(0, 0),))
    with pytest.raises(ValueError, match="intersection"):
        validate_layout(device, AtomLayout((atom,), (grid,)))

    row_without_column = AODGrid("aod", (AODLine(0, 0),), ())
    validate_layout(device, AtomLayout((atom,), (row_without_column,)))


def test_validate_layout_accepts_coordinate_roundoff(device: Device) -> None:
    atom = AtomState(0, Position(1e-10, 0), SLMHolder("home"))
    validate_layout(device, AtomLayout((atom,)))
