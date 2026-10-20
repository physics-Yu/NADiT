"""One-pair placement on fixed traps and an AOD readout region."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
)
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.state import AODHolder, AtomLayout, AtomState, SLMHolder


def _device() -> Device:
    return Device(
        "pair",
        (
            Zone("storage", Region(0, 0, 5, 0), ("rotate", "reset")),
            Zone("gate", Region(0, 10, 10, 10), ("cz", "rotate", "reset")),
            Zone("readout", Region(0, 20, 10, 22), ("measure",)),
        ),
        (
            SLMSite("s0", Position(0, 0)),
            SLMSite("s1", Position(5, 0)),
            SLMSite("g0", Position(0, 10)),
            SLMSite("g1", Position(2, 10)),
            SLMSite("g2", Position(8, 10)),
            SLMSite("g3", Position(10, 10)),
        ),
        (AOD("a", Region(-1, -1, 30, 30), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5, (("slm", "slm"),)),
        readout=ReadoutCapability("camera", 10, min_spacing_um=5),
        local=LocalControl("raman", 1, 2, ("slm", "aod")),
    )


def _layout() -> AtomLayout:
    return AtomLayout(
        (
            AtomState(9, Position(5, 0), SLMHolder("s1")),
            AtomState(4, Position(0, 0), SLMHolder("s0")),
        ),
        qubit_to_atom=(9, 4),
    )


def _dag(name: str, qubits: tuple[int, ...] = (0,)) -> PhysicalDAG:
    return PhysicalDAG(
        2,
        1,
        (
            PhysicalOperation(
                0,
                name,
                qubits,
                (0,) if name == "measure" else (),
                (0.5,) if name in ("rx", "ry", "rz") else (),
            ),
        ),
        (),
    )


def test_cz_chooses_nearest_pair_and_assignment() -> None:
    plan = PairPlacer("a").place(
        _dag("cz", (0, 1)), GateLayer((0,)), _layout(), _device()
    )

    assert plan.execution_layout.atom(9).holder == SLMHolder("g1")
    assert plan.execution_layout.atom(4).holder == SLMHolder("g0")
    assert plan.execution_layout.qubit_to_atom == (9, 4)
    assert plan.exit_layout is plan.execution_layout


def test_repeated_cz_reuses_current_gate_positions() -> None:
    placer = PairPlacer("a")
    dag = _dag("cz", (0, 1))
    first = placer.place(dag, GateLayer((0,)), _layout(), _device())
    second = placer.place(
        dag, GateLayer((0,)), first.exit_layout, _device(), reuse_qubits=(0, 1)
    )

    assert second.execution_layout == first.execution_layout


def test_cz_pair_must_share_one_permitted_zone() -> None:
    device = replace(
        _device(),
        zones=(
            Zone("left", Region(0, 10, 0, 10), ("cz",)),
            Zone("right", Region(2, 10, 2, 10), ("cz",)),
        ),
    )
    with pytest.raises(ValueError, match="CZ"):
        PairPlacer("a").place(_dag("cz", (0, 1)), GateLayer((0,)), _layout(), device)


def test_cz_requires_reachable_sites_within_radius_and_allowed_holders() -> None:
    device = _device()
    assert device.cz is not None
    for capability in (
        replace(device.cz, radius_um=1),
        replace(device.cz, holder_pairs=(("aod", "slm"),)),
    ):
        with pytest.raises(ValueError, match="CZ"):
            PairPlacer("a").place(
                _dag("cz", (0, 1)),
                GateLayer((0,)),
                _layout(),
                replace(device, cz=capability),
            )


@pytest.mark.parametrize("name", ["rx", "ry", "rz", "reset"])
def test_local_operation_keeps_valid_positions(name: str) -> None:
    layout = _layout()
    plan = PairPlacer("a").place(_dag(name), GateLayer((0,)), layout, _device())
    assert plan.execution_layout is layout
    assert plan.exit_layout is layout


def test_local_operation_moves_to_nearest_vacant_permitted_trap() -> None:
    device = replace(_device(), zones=(_device().zones[1],))
    layout = _layout()
    plan = PairPlacer("a").place(_dag("rx"), GateLayer((0,)), layout, device)

    assert plan.execution_layout.atom(9).holder == SLMHolder("g1")
    assert plan.execution_layout.atom(4) == layout.atom(4)
    assert plan.exit_layout is plan.execution_layout


def test_local_operation_honors_holder_capability() -> None:
    device = _device()
    assert device.local is not None
    device = replace(device, local=replace(device.local, holders=("aod",)))
    with pytest.raises(ValueError, match="local"):
        PairPlacer("a").place(_dag("rx"), GateLayer((0,)), _layout(), device)


def test_measurement_remains_in_aod_and_returns_to_entry_layout() -> None:
    layout = _layout()
    plan = PairPlacer("a").place(_dag("measure"), GateLayer((0,)), layout, _device())
    measured = plan.execution_layout.atom(9)

    assert measured.position == Position(5, 20)
    assert measured.holder == AODHolder("a", 0, 0)
    assert plan.execution_layout.grid("a").position(0, 0) == measured.position
    assert plan.execution_layout.atom(4) == layout.atom(4)
    assert plan.exit_layout is layout


def test_readout_can_choose_corner_to_separate_stationary_atom() -> None:
    device = replace(
        _device(),
        zones=(Zone("readout", Region(0, 0, 10, 0), ("measure",)),),
    )
    layout = AtomLayout(
        (
            AtomState(9, Position(2, 0), SLMHolder("g1")),
            AtomState(4, Position(0, 0), SLMHolder("s0")),
        ),
        qubit_to_atom=(9, 4),
    )
    plan = PairPlacer("a").place(_dag("measure"), GateLayer((0,)), layout, device)

    assert plan.execution_layout.atom(9).position == Position(10, 0)


def test_readout_requires_region_in_aod_workspace() -> None:
    device = replace(
        _device(),
        zones=(Zone("readout", Region(100, 100, 110, 110), ("measure",)),),
    )
    with pytest.raises(ValueError, match="readout"):
        PairPlacer("a").place(_dag("measure"), GateLayer((0,)), _layout(), device)


def test_readout_requires_aod_support_and_atom_retention() -> None:
    device = _device()
    assert device.readout is not None
    for capability in (
        replace(device.readout, holders=("slm",)),
        replace(device.readout, retain_atoms=False),
    ):
        with pytest.raises(ValueError, match="readout"):
            PairPlacer("a").place(
                _dag("measure"),
                GateLayer((0,)),
                _layout(),
                replace(device, readout=capability),
            )
