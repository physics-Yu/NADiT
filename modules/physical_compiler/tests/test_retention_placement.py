"""Explicit retained snapshots and constructive CZ preparation layouts."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from nadit_physical_compiler import motion, state
from nadit_physical_compiler.capabilities import CZCapability
from nadit_physical_compiler.contracts import GateLayer, Placer
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
from nadit_physical_compiler.placement.multipair import MultiPairPlacer
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.transport.strict import StrictTransport


def _device(storage_count: int = 6) -> Device:
    return Device(
        "retention",
        (
            Zone("storage", Region(0, 0, 30, 0), ()),
            Zone("gate", Region(-1, 99, 11, 101), ("cz",)),
        ),
        tuple(SLMSite(f"s{i}", Position(i * 5, 0)) for i in range(storage_count))
        + tuple(
            SLMSite(f"g{i}", Position(x, 100)) for i, x in enumerate((0, 2, 8, 10))
        ),
        (AOD("a", Region(-1, -1, 35, 120), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )


@pytest.fixture(params=[MultiPairPlacer, HeuristicPlacer], ids=["greedy", "ids"])
def make_placer(
    request: pytest.FixtureRequest,
) -> Callable[[Device], Placer]:
    constructor: type[MultiPairPlacer] | type[HeuristicPlacer] = request.param

    def make(device: Device) -> Placer:
        return constructor(
            "a",
            GatePairCatalog.from_device(device, "gate", (("g0", "g1"), ("g2", "g3"))),
        )

    return make


def _layout(device: Device, sites: tuple[str, ...]) -> AtomLayout:
    ids = tuple(17 + index * 5 for index in range(len(sites)))
    return AtomLayout(
        tuple(
            reversed(
                tuple(
                    AtomState(atom, device.site(site).position, SLMHolder(site))
                    for atom, site in zip(ids, sites, strict=True)
                )
            )
        ),
        qubit_to_atom=ids,
    )


def _dag() -> PhysicalDAG:
    return PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "cz", (0, 1), (), ()),
        ),
        ((0, 1),),
    )


def test_gate_positions_without_retention_are_cleared_before_assignment(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("g0", "g2"))
    plan = make_placer(device).place(_dag(), GateLayer((0,)), entry, device)

    preparation = plan.preparation_layout
    assert preparation is not None
    assert all(atom.position.y == 0 for atom in preparation.atoms)
    assert preparation.qubit_to_atom == entry.qubit_to_atom
    assert tuple(atom.id for atom in preparation.atoms) == tuple(
        atom.id for atom in entry.atoms
    )
    transport = StrictTransport("a")
    prepared = transport.construct(entry, preparation, device)
    arrived = transport.construct(prepared.exit_layout, plan.execution_layout, device)
    returned = transport.construct(arrived.exit_layout, plan.exit_layout, device)
    assert prepared.steps
    assert returned.exit_layout == plan.exit_layout


def test_one_explicit_anchor_survives_while_accidental_partner_location_is_cleared(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("g0", "g2"))
    anchor = entry.atom(17)
    plan = make_placer(device).place(
        _dag(), GateLayer((0,)), entry, device, retained_atoms=(anchor,)
    )

    preparation = plan.preparation_layout
    assert preparation is not None
    assert preparation.atom(17) == anchor
    assert preparation.atom(22).position.y == 0
    assert plan.execution_layout.atom(17) == anchor
    assert plan.execution_layout.atom(22).holder == SLMHolder("g1")


def test_stale_snapshot_does_not_anchor_an_atom_at_a_different_gate_site(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("g2", "g1"))
    stale = AtomState(17, device.site("g0").position, SLMHolder("g0"))
    second = entry.atom(22)
    plan = make_placer(device).place(
        _dag(), GateLayer((0,)), entry, device, retained_atoms=(stale, second)
    )

    assert plan.preparation_layout is not None
    assert plan.preparation_layout.atom(17).position.y == 0
    assert plan.preparation_layout.atom(22) == second
    assert plan.execution_layout.atom(17).holder == SLMHolder("g0")
    assert plan.execution_layout.atom(22) == second


def test_matching_spectator_snapshot_does_not_make_it_a_current_anchor(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("s0", "s1"))
    spectator = AtomState(99, device.site("g3").position, SLMHolder("g3"))
    entry = replace(entry, atoms=entry.atoms + (spectator,))
    plan = make_placer(device).place(
        _dag(), GateLayer((0,)), entry, device, retained_atoms=(spectator,)
    )

    assert plan.preparation_layout is not None
    assert plan.preparation_layout.atom(99).position.y == 0
    assert plan.execution_layout.atom(99) == plan.preparation_layout.atom(99)


def test_conflicting_valid_retention_is_still_reported(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("g0", "g2"))
    with pytest.raises(ValueError, match="anchor"):
        make_placer(device).place(
            _dag(), GateLayer((0,)), entry, device, retained_atoms=entry.atoms
        )


def test_preparation_needs_initially_vacant_storage_only_for_cleared_atoms(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device(storage_count=0)
    entry = _layout(device, ("g0", "g1"))
    placer = make_placer(device)
    with pytest.raises(ValueError, match="storage"):
        placer.place(_dag(), GateLayer((0,)), entry, device)

    kept = placer.place(
        _dag(),
        GateLayer((0,)),
        entry,
        device,
        retained_atoms=entry.atoms,
        reuse_qubits=(0, 1),
        following=GateLayer((1,)),
    )
    assert kept.preparation_layout is None
    assert kept.exit_layout == entry


def test_storage_entry_does_not_add_a_preparation_phase(
    make_placer: Callable[[Device], Placer],
) -> None:
    device = _device()
    entry = _layout(device, ("s0", "s1"))
    plan = make_placer(device).place(_dag(), GateLayer((0,)), entry, device)
    assert plan.preparation_layout is None


def test_preparation_does_not_call_full_device_checks(
    make_placer: Callable[[Device], Placer], monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _device()
    entry = _layout(device, ("g0", "g2"))

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Preparation must construct rather than revalidate.")

    monkeypatch.setattr(state, "validate_layout", unexpected)
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)
    plan = make_placer(device).place(_dag(), GateLayer((0,)), entry, device)
    assert plan.preparation_layout is not None
