"""Multi-pair execution layouts, spectator evacuation, and storage returns."""

from dataclasses import replace
from itertools import combinations
from math import hypot

import pytest

from nadit_physical_compiler import motion, state
from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.coverage import RegionSet
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
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.state import AODHolder, AtomLayout, AtomState, SLMHolder


def _device() -> Device:
    return Device(
        "multipair",
        (
            Zone("storage", Region(0, 0, 35, 0), ("rotate", "reset")),
            Zone("gate", Region(-1, 99, 11, 101), ("cz", "rotate", "reset")),
            Zone("readout", Region(0, 200, 35, 205), ("measure",)),
        ),
        slm_grid("s", Position(0, 0), 1, 8, Position(5, 5))
        + tuple(
            SLMSite(f"g{index}", Position(x, 100))
            for index, x in enumerate((0, 2, 8, 10))
        )
        + (SLMSite("spectator", Position(5, 100)),),
        (AOD("a", Region(-1, -1, 35, 205), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
        local=LocalControl("raman", 1, 2),
        readout=ReadoutCapability("camera", 10, min_spacing_um=5),
    )


def _catalog(device: Device) -> GatePairCatalog:
    return GatePairCatalog.from_device(device, "gate", (("g0", "g1"), ("g2", "g3")))


def _layout(device: Device, count: int = 4) -> AtomLayout:
    ids = tuple(20 + index for index in range(count))
    return AtomLayout(
        tuple(
            AtomState(
                atom,
                device.site(f"s_r0_c{index}").position,
                SLMHolder(f"s_r0_c{index}"),
            )
            for index, atom in enumerate(ids)
        ),
        qubit_to_atom=ids,
    )


def _cz_dag(pairs: tuple[tuple[int, int], ...], num_qubits: int = 4) -> PhysicalDAG:
    return PhysicalDAG(
        num_qubits,
        0,
        tuple(
            PhysicalOperation(index, "cz", pair, (), ())
            for index, pair in enumerate(pairs)
        ),
        (),
    )


def _interaction_edges(
    layout: AtomLayout, coverage: Region | RegionSet, radius: float
) -> set[frozenset[int]]:
    illuminated = [atom for atom in layout.atoms if coverage.contains(atom.position)]
    return {
        frozenset((a.id, b.id))
        for a, b in combinations(illuminated, 2)
        if hypot(a.position.x - b.position.x, a.position.y - b.position.y) <= radius
    }


def test_two_gates_occupy_distinct_pairs_with_exact_intended_interactions() -> None:
    device = _device()
    catalog = _catalog(device)
    entry = _layout(device)
    dag = _cz_dag(((0, 1), (2, 3)))
    plan = MultiPairPlacer("a", catalog).place(dag, GateLayer((0, 1)), entry, device)

    assert _interaction_edges(plan.execution_layout, catalog.coverage, 4.6) == {
        frozenset((20, 21)),
        frozenset((22, 23)),
    }
    assert len({atom.position for atom in plan.execution_layout.atoms}) == 4
    assert tuple(atom.id for atom in plan.execution_layout.atoms) == (20, 21, 22, 23)
    assert plan.execution_layout.qubit_to_atom == entry.qubit_to_atom
    assert plan.exit_layout == entry


def test_partner_change_starts_from_the_retained_storage_assignments() -> None:
    device = _device()
    catalog = _catalog(device)
    placer = MultiPairPlacer("a", catalog)
    first = placer.place(
        _cz_dag(((0, 1), (2, 3))), GateLayer((0, 1)), _layout(device), device
    )
    second = placer.place(
        _cz_dag(((0, 2), (1, 3))), GateLayer((0, 1)), first.exit_layout, device
    )

    assert _interaction_edges(second.execution_layout, catalog.coverage, 4.6) == {
        frozenset((20, 22)),
        frozenset((21, 23)),
    }
    assert second.exit_layout == first.exit_layout


def test_spare_spectator_inside_coverage_is_evacuated_before_pulse() -> None:
    device = _device()
    catalog = _catalog(device)
    entry = _layout(device)
    spectator = AtomState(99, device.site("spectator").position, SLMHolder("spectator"))
    entry = replace(entry, atoms=entry.atoms + (spectator,))
    plan = MultiPairPlacer("a", catalog).place(
        _cz_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device
    )

    parked = plan.execution_layout.atom(99)
    assert not catalog.coverage.contains(parked.position)
    assert parked == plan.exit_layout.atom(99)
    assert isinstance(parked.holder, SLMHolder)
    assert parked.holder.site_id == "s_r0_c4"
    assert plan.exit_layout.qubit_to_atom == entry.qubit_to_atom
    assert _interaction_edges(plan.execution_layout, catalog.coverage, 4.6) == {
        frozenset((20, 21)),
        frozenset((22, 23)),
    }


def test_all_gate_participants_return_to_storage_including_initial_gate_atoms() -> None:
    device = _device()
    catalog = _catalog(device)
    entry = _layout(device)
    entry = replace(
        entry,
        atoms=(
            AtomState(20, device.site("g0").position, SLMHolder("g0")),
            *entry.atoms[1:],
        ),
    )
    plan = MultiPairPlacer("a", catalog).place(
        _cz_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device
    )

    assert all(
        not catalog.coverage.contains(atom.position) for atom in plan.exit_layout.atoms
    )
    assert plan.exit_layout.atom(20).holder == SLMHolder("s_r0_c0")
    assert plan.exit_layout.atoms[1:] == entry.atoms[1:]


def test_other_occupied_storage_sites_are_reserved() -> None:
    device = _device()
    catalog = _catalog(device)
    entry = _layout(device, 5)
    entry = replace(
        entry,
        atoms=(
            AtomState(20, device.site("g0").position, SLMHolder("g0")),
            *entry.atoms[1:],
            AtomState(99, device.site("s_r0_c0").position, SLMHolder("s_r0_c0")),
        ),
    )
    plan = MultiPairPlacer("a", catalog).place(
        _cz_dag(((0, 1),), num_qubits=5), GateLayer((0,)), entry, device
    )

    assert plan.exit_layout.atom(20).holder == SLMHolder("s_r0_c5")
    assert plan.execution_layout.atom(99) == entry.atom(99)
    assert plan.execution_layout.atom(24) == entry.atom(24)


def test_insufficient_gate_capacity_is_reported() -> None:
    device = _device()
    with pytest.raises(ValueError, match="capacity"):
        MultiPairPlacer("a", _catalog(device)).place(
            _cz_dag(((0, 1), (2, 3), (4, 5)), num_qubits=6),
            GateLayer((0, 1, 2)),
            _layout(device, 6),
            device,
        )


def test_storage_shortfall_is_reported_for_spectator_evacuation() -> None:
    device = _device()
    catalog = _catalog(device)
    device = replace(
        device,
        zones=(
            Zone("storage", Region(0, 0, 15, 0), ("rotate", "reset")),
            *device.zones[1:],
        ),
    )
    entry = _layout(device)
    entry = replace(
        entry,
        atoms=entry.atoms
        + (AtomState(99, device.site("spectator").position, SLMHolder("spectator")),),
    )
    with pytest.raises(ValueError, match="storage"):
        MultiPairPlacer("a", catalog).place(
            _cz_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device
        )


@pytest.mark.parametrize("name", ["rx", "reset", "measure"])
def test_non_cz_operations_delegate_existing_local_and_readout_placement(
    name: str,
) -> None:
    device = _device()
    entry = _layout(device, 6)
    dag = PhysicalDAG(
        6,
        1,
        (
            PhysicalOperation(
                0,
                name,
                (4,),
                (0,) if name == "measure" else (),
                (0.5,) if name == "rx" else (),
            ),
        ),
        (),
    )
    plan = MultiPairPlacer("a", _catalog(device)).place(
        dag, GateLayer((0,)), entry, device
    )
    if name == "measure":
        assert isinstance(plan.execution_layout.atom(24).holder, AODHolder)
        assert plan.exit_layout is entry
    else:
        assert plan.execution_layout is entry
        assert plan.exit_layout is entry


def test_placement_uses_catalogue_without_full_device_rechecks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _device()
    catalog = _catalog(device)
    entry = _layout(device)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Catalogue-driven placement must not recheck transport.")

    monkeypatch.setattr(state, "validate_layout", unexpected)
    monkeypatch.setattr(motion, "check_move", unexpected)
    monkeypatch.setattr(GatePairCatalog, "from_device", unexpected)
    plan = MultiPairPlacer("a", catalog).place(
        _cz_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device
    )
    assert plan.exit_layout == entry
