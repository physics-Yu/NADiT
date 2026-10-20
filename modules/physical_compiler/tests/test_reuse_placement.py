"""Gate-site anchors and storage allocation across known neighboring CZ layers."""

from dataclasses import replace
from itertools import combinations
from math import hypot

import pytest

from nadit_physical_compiler.capabilities import CZCapability
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
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder


def _device(storage_count: int = 6) -> Device:
    return Device(
        "reuse",
        (
            Zone("storage", Region(0, 0, 30, 0), ()),
            Zone("gate", Region(-1, 99, 11, 101), ("cz",)),
        ),
        tuple(
            SLMSite(f"s{index}", Position(index * 5, 0))
            for index in range(storage_count)
        )
        + tuple(
            SLMSite(f"g{index}", Position(x, 100))
            for index, x in enumerate((0, 2, 8, 10))
        ),
        (AOD("a", Region(-1, -1, 35, 120), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )


def _placer(device: Device) -> MultiPairPlacer:
    return MultiPairPlacer(
        "a", GatePairCatalog.from_device(device, "gate", (("g0", "g1"), ("g2", "g3")))
    )


def _layout(device: Device, sites: tuple[str, ...]) -> AtomLayout:
    ids = (42, 101, 8, 5)[: len(sites)]
    atoms = tuple(
        AtomState(atom, device.site(site).position, SLMHolder(site))
        for atom, site in zip(ids, sites, strict=True)
    )
    return AtomLayout(tuple(reversed(atoms)), qubit_to_atom=ids)


def _dag(pairs: tuple[tuple[int, int], ...], num_qubits: int = 4) -> PhysicalDAG:
    return PhysicalDAG(
        num_qubits,
        0,
        tuple(
            PhysicalOperation(index, "cz", pair, (), (), source_id=100 + index)
            for index, pair in enumerate(pairs)
        ),
        (),
    )


def _edges(layout: AtomLayout, catalog: GatePairCatalog) -> set[frozenset[int]]:
    atoms = [atom for atom in layout.atoms if catalog.coverage.contains(atom.position)]
    return {
        frozenset((a.id, b.id))
        for a, b in combinations(atoms, 2)
        if hypot(a.position.x - b.position.x, a.position.y - b.position.y) <= 4.6
    }


def test_swapped_partners_keep_two_matched_anchors_then_return_to_storage() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))
    first = placer.place(
        _dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device, reuse_qubits=(0, 3)
    )

    for qubit in (0, 3):
        atom_id = entry.qubit_to_atom[qubit]
        assert first.exit_layout.atom(atom_id) == first.execution_layout.atom(atom_id)
        assert placer.catalog.coverage.contains(
            first.exit_layout.atom(atom_id).position
        )
    for qubit in (1, 2):
        atom_id = entry.qubit_to_atom[qubit]
        assert not placer.catalog.coverage.contains(
            first.exit_layout.atom(atom_id).position
        )

    second = placer.place(
        _dag(((0, 2), (1, 3))), GateLayer((0, 1)), first.exit_layout, device
    )
    assert second.execution_layout.atom(42) == first.exit_layout.atom(42)
    assert second.execution_layout.atom(5) == first.exit_layout.atom(5)
    assert _edges(second.execution_layout, placer.catalog) == {
        frozenset((42, 8)),
        frozenset((101, 5)),
    }
    assert all(
        not placer.catalog.coverage.contains(atom.position)
        for atom in second.exit_layout.atoms
    )
    assert second.exit_layout.qubit_to_atom == entry.qubit_to_atom
    assert tuple(atom.id for atom in second.exit_layout.atoms) == tuple(
        atom.id for atom in entry.atoms
    )


def test_all_anchored_pairs_are_reserved_before_an_earlier_unanchored_gate() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "g0", "s3"))
    plan = placer.place(_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device)

    assert plan.execution_layout.atom(8) == entry.atom(8)
    assert plan.execution_layout.atom(5).holder == SLMHolder("g1")
    assert {plan.execution_layout.atom(atom_id).holder for atom_id in (42, 101)} == {
        SLMHolder("g2"),
        SLMHolder("g3"),
    }


def test_identical_gate_keeps_both_atoms_and_needs_no_storage_between_layers() -> None:
    device = _device()
    placer = _placer(device)
    dag = _dag(((0, 1),), num_qubits=2)
    entry = _layout(device, ("s0", "s1"))
    first = placer.place(dag, GateLayer((0,)), entry, device, reuse_qubits=(0, 1))
    second = placer.place(
        dag, GateLayer((0,)), first.exit_layout, device, reuse_qubits=(0, 1)
    )
    final = placer.place(dag, GateLayer((0,)), second.exit_layout, device)

    assert first.exit_layout == first.execution_layout
    assert second.execution_layout == first.exit_layout
    assert second.exit_layout == second.execution_layout
    assert final.execution_layout == second.exit_layout
    assert all(
        not placer.catalog.coverage.contains(atom.position)
        for atom in final.exit_layout.atoms
    )


def test_retained_atom_frees_its_storage_site_for_the_returning_partner() -> None:
    device = _device(storage_count=1)
    placer = _placer(device)
    entry = _layout(device, ("s0", "g1"))
    dag = _dag(((0, 1),), num_qubits=2)

    plan = placer.place(dag, GateLayer((0,)), entry, device, reuse_qubits=(0,))

    assert plan.exit_layout.atom(42).holder == SLMHolder("g0")
    assert plan.exit_layout.atom(101).holder == SLMHolder("s0")
    assert _edges(plan.execution_layout, placer.catalog) == {frozenset((42, 101))}
    with pytest.raises(ValueError, match="storage"):
        placer.place(dag, GateLayer((0,)), entry, device)


def test_two_retained_atoms_do_not_require_any_storage_slot() -> None:
    device = _device(storage_count=0)
    placer = _placer(device)
    entry = _layout(device, ("g0", "g1"))
    plan = placer.place(
        _dag(((0, 1),), num_qubits=2),
        GateLayer((0,)),
        entry,
        device,
        reuse_qubits=(0, 1),
    )

    assert plan.execution_layout == plan.exit_layout == entry


def test_unused_previous_anchor_is_evacuated_as_a_spectator() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "g2"))
    plan = placer.place(
        _dag(((0, 1),)), GateLayer((0,)), entry, device, reuse_qubits=(0,)
    )

    assert not placer.catalog.coverage.contains(plan.execution_layout.atom(5).position)
    assert plan.execution_layout.atom(5) == plan.exit_layout.atom(5)
    assert _edges(plan.execution_layout, placer.catalog) == {frozenset((42, 101))}
    assert placer.catalog.coverage.contains(plan.exit_layout.atom(42).position)


def test_spectator_prefers_initially_vacant_storage() -> None:
    device = _device(storage_count=3)
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1"))
    entry = replace(
        entry,
        atoms=entry.atoms
        + (AtomState(99, device.site("g0").position, SLMHolder("g0")),),
    )
    plan = placer.place(
        _dag(((0, 1),), num_qubits=2),
        GateLayer((0,)),
        entry,
        device,
        reuse_qubits=(0,),
    )

    assert plan.execution_layout.atom(99).holder == SLMHolder("s2")
    assert plan.exit_layout.atom(99) == plan.execution_layout.atom(99)
    assert plan.execution_layout.atom(42).holder == SLMHolder("g0")


def test_relocated_anchor_is_replanned_from_its_actual_holder() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))
    first = placer.place(
        _dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device, reuse_qubits=(0, 3)
    )
    relocated = replace(
        first.exit_layout,
        atoms=tuple(
            AtomState(42, device.site("s4").position, SLMHolder("s4"))
            if atom.id == 42
            else atom
            for atom in first.exit_layout.atoms
        ),
    )
    plan = placer.place(_dag(((0, 2), (1, 3))), GateLayer((0, 1)), relocated, device)

    assert plan.execution_layout.atom(5) == relocated.atom(5)
    assert _edges(plan.execution_layout, placer.catalog) == {
        frozenset((42, 8)),
        frozenset((101, 5)),
    }


@pytest.mark.parametrize(
    ("sites", "gates"),
    [
        (("g0", "g2", "s2", "s3"), ((0, 1),)),
        (("g0", "s1", "g1", "s3"), ((0, 1), (2, 3))),
    ],
)
def test_incompatible_retained_anchors_have_an_explicit_construction_error(
    sites: tuple[str, ...], gates: tuple[tuple[int, int], ...]
) -> None:
    device = _device()
    with pytest.raises(ValueError, match="anchor"):
        _placer(device).place(
            _dag(gates),
            GateLayer(tuple(range(len(gates)))),
            _layout(device, sites),
            device,
        )
