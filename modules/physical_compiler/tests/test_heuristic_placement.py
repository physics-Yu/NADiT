"""IDS gate assignments, optional retention, and legal occupied geometry."""

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
from nadit_physical_compiler.placement.catalog import GatePairCatalog
from nadit_physical_compiler.placement.heuristic import HeuristicPlacer
from nadit_physical_compiler.placement.scoring import PlacementConfig
from nadit_physical_compiler.state import AODHolder, AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.transport.strict import StrictTransport


def _device(storage_count: int = 8) -> Device:
    return Device(
        "ids",
        (
            Zone("storage", Region(0, 0, 35, 0), ("rotate", "reset")),
            Zone("gate", Region(-1, 99, 11, 101), ("cz", "rotate", "reset")),
            Zone("readout", Region(0, 200, 35, 205), ("measure",)),
        ),
        tuple(SLMSite(f"s{i}", Position(5 * i, 0)) for i in range(storage_count))
        + tuple(SLMSite(f"g{i}", Position(x, 100)) for i, x in enumerate((0, 2, 8, 10)))
        + (SLMSite("spectator", Position(5, 100)),),
        (AOD("a", Region(-1, -1, 35, 205), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
        local=LocalControl("raman", 1, 2),
        readout=ReadoutCapability("camera", 10, min_spacing_um=5),
    )


def _placer(device: Device, **options: float | int) -> HeuristicPlacer:
    return HeuristicPlacer(
        "a",
        GatePairCatalog.from_device(device, "gate", (("g0", "g1"), ("g2", "g3"))),
        config=PlacementConfig(**options),  # type: ignore[arg-type]
    )


def _layout(device: Device, sites: tuple[str, ...]) -> AtomLayout:
    ids = tuple(17 + 3 * index for index in range(len(sites)))
    return AtomLayout(
        tuple(
            AtomState(atom, device.site(site).position, SLMHolder(site))
            for atom, site in zip(ids, sites, strict=True)
        ),
        qubit_to_atom=ids,
    )


def _dag(pairs: tuple[tuple[int, int], ...], num_qubits: int = 4) -> PhysicalDAG:
    return PhysicalDAG(
        num_qubits,
        0,
        tuple(
            PhysicalOperation(index, "cz", pair, (), ())
            for index, pair in enumerate(pairs)
        ),
        (),
    )


def _edges(layout: AtomLayout, coverage: Region) -> set[frozenset[int]]:
    atoms = [atom for atom in layout.atoms if coverage.contains(atom.position)]
    return {
        frozenset((a.id, b.id))
        for a, b in combinations(atoms, 2)
        if hypot(a.position.x - b.position.x, a.position.y - b.position.y) <= 4.6
    }


def test_gate_assignment_has_only_intended_edges_and_returns_without_lookahead() -> (
    None
):
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))
    plan = placer.place(_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device)

    assert _edges(plan.execution_layout, placer.catalog.coverage) == {
        frozenset((17, 20)),
        frozenset((23, 26)),
    }
    assert all(
        not placer.catalog.coverage.contains(atom.position)
        for atom in plan.exit_layout.atoms
    )
    assert tuple(atom.id for atom in plan.exit_layout.atoms) == entry.qubit_to_atom
    transport = StrictTransport("a")
    arrival = transport.construct(entry, plan.execution_layout, device)
    departure = transport.construct(arrival.exit_layout, plan.exit_layout, device)
    assert departure.exit_layout == plan.exit_layout


def test_matching_hint_can_select_stay_for_an_identical_following_pair() -> None:
    device = _device()
    placer = _placer(device)
    dag = _dag(((0, 1), (0, 1)), num_qubits=2)
    entry = _layout(device, ("s0", "s1"))
    plan = placer.place(
        dag,
        GateLayer((0,)),
        entry,
        device,
        reuse_qubits=(0, 1),
        following=GateLayer((1,)),
    )

    assert plan.exit_layout == plan.execution_layout
    assert all(
        placer.catalog.coverage.contains(a.position) for a in plan.exit_layout.atoms
    )


def test_matching_hint_can_be_declined_by_the_configured_reuse_score() -> None:
    device = _device()
    placer = _placer(device, reuse_level=-1000)
    dag = _dag(((0, 1), (0, 1)), num_qubits=2)
    plan = placer.place(
        dag,
        GateLayer((0,)),
        _layout(device, ("s0", "s1")),
        device,
        reuse_qubits=(0, 1),
        following=GateLayer((1,)),
    )

    assert all(
        not placer.catalog.coverage.contains(atom.position)
        for atom in plan.exit_layout.atoms
    )


def test_hint_without_a_following_layer_does_not_retain_atoms() -> None:
    device = _device()
    placer = _placer(device)
    plan = placer.place(
        _dag(((0, 1),), num_qubits=2),
        GateLayer((0,)),
        _layout(device, ("s0", "s1")),
        device,
        reuse_qubits=(0, 1),
    )

    assert all(
        not placer.catalog.coverage.contains(atom.position)
        for atom in plan.exit_layout.atoms
    )


def test_actual_gate_holders_anchor_new_partners_before_unanchored_jobs() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "g0", "s3"))
    plan = placer.place(_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device)

    assert plan.execution_layout.atom(23) == entry.atom(23)
    assert plan.execution_layout.atom(26).holder == SLMHolder("g1")
    assert {plan.execution_layout.atom(atom).holder for atom in (17, 20)} == {
        SLMHolder("g2"),
        SLMHolder("g3"),
    }


def test_spare_spectator_is_moved_to_initially_vacant_storage_before_pulse() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))
    entry = replace(
        entry,
        atoms=entry.atoms
        + (AtomState(99, device.site("g0").position, SLMHolder("g0")),),
    )
    plan = placer.place(_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device)

    assert plan.execution_layout.atom(99).holder == SLMHolder("s4")
    assert plan.exit_layout.atom(99) == plan.execution_layout.atom(99)
    assert _edges(plan.execution_layout, placer.catalog.coverage) == {
        frozenset((17, 20)),
        frozenset((23, 26)),
    }
    assert (
        StrictTransport("a").construct(entry, plan.execution_layout, device).exit_layout
        == plan.execution_layout
    )


def test_stationary_storage_positions_are_not_search_options() -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("g0", "g1", "s0", "s1"))
    plan = placer.place(_dag(((0, 1),)), GateLayer((0,)), entry, device)

    assert plan.exit_layout.atom(23) == entry.atom(23)
    assert plan.exit_layout.atom(26) == entry.atom(26)
    assert all(
        plan.exit_layout.atom(atom).holder not in (SLMHolder("s0"), SLMHolder("s1"))
        for atom in (17, 20)
    )


def test_retention_needs_no_reserved_storage_slot() -> None:
    device = _device(storage_count=0)
    placer = _placer(device)
    entry = _layout(device, ("g0", "g1"))
    dag = _dag(((0, 1), (0, 1)), num_qubits=2)
    plan = placer.place(
        dag,
        GateLayer((0,)),
        entry,
        device,
        reuse_qubits=(0, 1),
        following=GateLayer((1,)),
    )

    assert plan.execution_layout == plan.exit_layout == entry


def test_pair_capacity_shortfall_is_reported() -> None:
    device = _device()
    with pytest.raises(ValueError, match="capacity"):
        _placer(device).place(
            _dag(((0, 1), (2, 3), (4, 5)), num_qubits=6),
            GateLayer((0, 1, 2)),
            _layout(device, ("s0", "s1", "s2", "s3", "s4", "s5")),
            device,
        )


@pytest.mark.parametrize("name", ["empty", "rx", "reset", "measure"])
def test_empty_local_and_readout_layers_keep_existing_behavior(name: str) -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))
    if name == "empty":
        dag, layer = PhysicalDAG(4, 0, (), ()), GateLayer(())
    else:
        dag = PhysicalDAG(
            4,
            1,
            (
                PhysicalOperation(
                    0,
                    name,
                    (0,),
                    (0,) if name == "measure" else (),
                    (0.5,) if name == "rx" else (),
                ),
            ),
            (),
        )
        layer = GateLayer((0,))
    plan = placer.place(dag, layer, entry, device)

    assert plan.exit_layout == entry
    if name == "measure":
        assert isinstance(plan.execution_layout.atom(17).holder, AODHolder)
    else:
        assert plan.execution_layout == entry


def test_search_does_not_call_transport_or_full_geometry_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _device()
    placer = _placer(device)
    entry = _layout(device, ("s0", "s1", "s2", "s3"))

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Placement search must not emit transport or revalidate.")

    monkeypatch.setattr(StrictTransport, "construct", unexpected)
    monkeypatch.setattr(state, "validate_layout", unexpected)
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)
    assert placer.place(_dag(((0, 1), (2, 3))), GateLayer((0, 1)), entry, device)
