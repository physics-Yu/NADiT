"""Strategy retention records follow actual stays across fragment boundaries."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.atom_ir import (
    AtomAction,
    AtomProgram,
    AtomStep,
    CZPulse,
    Measure,
    Rotation,
)
from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.contracts import AtomFragment, GateLayer, PlacementPlan
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
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import SequentialTransport


@pytest.fixture
def device() -> Device:
    return Device(
        "retention",
        (Zone("operations", Region(-1, -1, 20, 120), ("cz", "rotate", "measure")),),
        tuple(
            SLMSite(name, Position(x, y))
            for name, x, y in (
                ("s0", 0, 0),
                ("s1", 5, 0),
                ("s2", 10, 0),
                ("g0", 0, 100),
                ("g1", 2, 100),
                ("g2", 8, 100),
            )
        ),
        (AOD("aod", Region(-10, -10, 30, 130), 1),),
        Timing(2, 3, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
        local=LocalControl("raman", 1, 1),
        readout=ReadoutCapability("camera", 2),
    )


def _layout(device: Device, sites: tuple[str, str, str]) -> AtomLayout:
    ids = (42, 7, 101)
    atoms = tuple(
        AtomState(atom_id, device.site(site).position, SLMHolder(site))
        for atom_id, site in zip(ids, sites, strict=True)
    )
    layout = AtomLayout(tuple(reversed(atoms)), qubit_to_atom=ids)
    validate_layout(device, layout)
    return layout


def _dag(name: str) -> PhysicalDAG:
    operation = PhysicalOperation(
        0,
        name,
        (0, 1) if name == "cz" else (0,),
        (0,) if name == "measure" else (),
        (0.3,) if name == "rx" else (),
        source_id=50,
    )
    return PhysicalDAG(
        3,
        1,
        (operation, PhysicalOperation(1, "cz", (0, 1), (), (), source_id=51)),
        ((0, 1),),
    )


class _Placer:
    """Return a selected plan while recording the strategy's metadata handoff."""

    def __init__(self, plan: PlacementPlan) -> None:
        self.plan = plan
        self.calls: list[tuple[tuple[int, ...], tuple[AtomState, ...]]] = []

    def place(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        reuse_qubits: tuple[int, ...] = (),
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> PlacementPlan:
        self.calls.append((reuse_qubits, retained_atoms))
        return self.plan


class _Reuse:
    def analyze(
        self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
    ) -> tuple[int, ...]:
        return (0, 1)


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[AtomLayout, AtomLayout]] = []
        self.fragments: list[AtomFragment] = []

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        self.calls.append((layout, target_layout))
        fragment = SequentialTransport("aod").construct(layout, target_layout, device)
        self.fragments.append(fragment)
        return fragment


class _Emitter:
    def __init__(self) -> None:
        self.layouts: list[AtomLayout] = []
        self.fragments: list[AtomFragment] = []

    def __call__(
        self, dag: PhysicalDAG, layer: GateLayer, layout: AtomLayout, device: Device
    ) -> AtomFragment:
        self.layouts.append(layout)
        node = dag.nodes[layer.operation_ids[0]]
        atoms = tuple(layout.qubit_to_atom[qubit] for qubit in node.qubits)
        action: AtomAction
        if node.name == "cz":
            action = CZPulse("rydberg", atoms, duration_us=0.5, source_ids=(node.id,))
        elif node.name == "measure":
            assert node.result is not None
            action = Measure(
                "camera",
                ((atoms[0], node.result),),
                True,
                duration_us=2,
                source_ids=(node.id,),
            )
        else:
            action = Rotation(
                "raman", atoms, "X", 0.3, duration_us=1, source_ids=(node.id,)
            )
        fragment = AtomFragment((AtomStep((action,)),), layout, layer.operation_ids)
        self.fragments.append(fragment)
        return fragment


def _strategy(
    plan: PlacementPlan, *, reuse: bool = False
) -> tuple[SearchSmarterStrategy, _Placer, _Transport, _Emitter]:
    placer, transport, emitter = _Placer(plan), _Transport(), _Emitter()
    return (
        SearchSmarterStrategy(
            ReadyScheduler(),
            placer,
            transport,
            emitter,
            reuse_analyzer=_Reuse() if reuse else None,
        ),
        placer,
        transport,
        emitter,
    )


def test_cz_records_only_outgoing_candidates_that_actually_stay(device: Device) -> None:
    entry = _layout(device, ("s0", "s1", "g2"))
    execution = _layout(device, ("g0", "g1", "g2"))
    final = _layout(device, ("g0", "s1", "g2"))
    incoming = (entry.atom(101),)
    strategy, placer, _, _ = _strategy(PlacementPlan(execution, final), reuse=True)

    fragment = strategy.lower(
        _dag("cz"),
        GateLayer((0,)),
        entry,
        device,
        following=GateLayer((1,)),
        retained_atoms=incoming,
    )

    assert placer.calls == [((0, 1), incoming)]
    assert fragment.retained_atoms == (execution.atom(42),)
    assert fragment.exit_layout == final
    assert replay(AtomProgram(fragment.steps), entry, device) == final


def test_declined_cz_reuse_and_terminal_cz_create_no_retention(device: Device) -> None:
    entry = _layout(device, ("s0", "s1", "s2"))
    execution = _layout(device, ("g0", "g1", "s2"))
    declined, _, _, _ = _strategy(PlacementPlan(execution, entry), reuse=True)

    result = declined.lower(
        _dag("cz"), GateLayer((0,)), entry, device, following=GateLayer((1,))
    )
    assert result.retained_atoms == ()

    terminal, _, _, _ = _strategy(PlacementPlan(execution, execution), reuse=True)
    result = terminal.lower(
        _dag("cz"),
        GateLayer((0,)),
        execution,
        device,
        retained_atoms=(execution.atom(42), execution.atom(7)),
    )
    assert result.retained_atoms == ()


@pytest.mark.parametrize("name", ["rx", "measure"])
def test_local_and_readout_preserve_existing_snapshots_without_inventing_new_ones(
    device: Device, name: str
) -> None:
    layout = _layout(device, ("g0", "s1", "g2"))
    incoming = (layout.atom(42),)
    strategy, placer, _, _ = _strategy(PlacementPlan(layout, layout))

    fragment = strategy.lower(
        _dag(name), GateLayer((0,)), layout, device, retained_atoms=incoming
    )

    assert placer.calls == [((), incoming)]
    assert fragment.retained_atoms == incoming
    assert fragment.exit_layout.atom(101).holder == SLMHolder("g2")
    without_record = strategy.lower(_dag(name), GateLayer((0,)), layout, device)
    assert without_record.retained_atoms == ()


@pytest.mark.parametrize("name", ["rx", "measure"])
def test_leaving_a_retained_site_clears_it_and_returning_does_not_restore_it(
    device: Device, name: str
) -> None:
    retained_layout = _layout(device, ("g0", "s1", "s2"))
    storage = _layout(device, ("s0", "s1", "s2"))
    outward, _, _, _ = _strategy(PlacementPlan(retained_layout, storage))
    inward, _, _, _ = _strategy(PlacementPlan(storage, retained_layout))

    leaving = outward.lower(
        _dag(name),
        GateLayer((0,)),
        retained_layout,
        device,
        retained_atoms=(retained_layout.atom(42),),
    )
    returning = inward.lower(
        _dag(name),
        GateLayer((0,)),
        leaving.exit_layout,
        device,
        retained_atoms=leaving.retained_atoms,
    )

    assert leaving.retained_atoms == returning.retained_atoms == ()
    assert returning.exit_layout.atom(42) == retained_layout.atom(42)


def test_a_stale_snapshot_does_not_revive_when_final_state_matches_it(
    device: Device,
) -> None:
    gate = _layout(device, ("g0", "s1", "s2"))
    storage = _layout(device, ("s0", "s1", "s2"))
    strategy, _, _, _ = _strategy(PlacementPlan(storage, gate))

    fragment = strategy.lower(
        _dag("rx"),
        GateLayer((0,)),
        storage,
        device,
        retained_atoms=(gate.atom(42),),
    )

    assert fragment.exit_layout.atom(42) == gate.atom(42)
    assert fragment.retained_atoms == ()


def test_changing_only_holder_clears_retention_across_readout_fragments(
    device: Device,
) -> None:
    stored = _layout(device, ("g0", "s1", "s2"))
    position = stored.atom(42).position
    carried = replace(
        stored,
        atoms=tuple(
            replace(atom, holder=AODHolder("aod", 3, 8)) if atom.id == 42 else atom
            for atom in stored.atoms
        ),
        aod_grids=(
            AODGrid("aod", (AODLine(3, position.y),), (AODLine(8, position.x),)),
        ),
    )
    validate_layout(device, carried)
    load, _, _, _ = _strategy(PlacementPlan(carried, carried))
    store, _, _, _ = _strategy(PlacementPlan(carried, stored))

    loaded = load.lower(
        _dag("measure"),
        GateLayer((0,)),
        stored,
        device,
        retained_atoms=(stored.atom(42),),
    )
    unloaded = store.lower(
        _dag("measure"),
        GateLayer((0,)),
        carried,
        device,
        retained_atoms=loaded.retained_atoms,
    )

    assert loaded.retained_atoms == unloaded.retained_atoms == ()
    assert unloaded.exit_layout.atom(42) == stored.atom(42)


def test_preparation_transport_precedes_execution_pulse_and_exit(
    device: Device,
) -> None:
    entry = _layout(device, ("g0", "s1", "s2"))
    preparation = _layout(device, ("s0", "s1", "s2"))
    execution = _layout(device, ("g1", "g0", "s2"))
    final = _layout(device, ("s0", "s1", "s2"))
    strategy, _, transport, emitter = _strategy(
        PlacementPlan(execution, final, preparation_layout=preparation)
    )

    fragment = strategy.lower(
        _dag("cz"),
        GateLayer((0,)),
        entry,
        device,
        retained_atoms=(entry.atom(42),),
    )

    assert transport.calls == [
        (entry, preparation),
        (preparation, execution),
        (execution, final),
    ]
    assert emitter.layouts == [execution]
    assert fragment.steps == (
        transport.fragments[0].steps
        + transport.fragments[1].steps
        + emitter.fragments[0].steps
        + transport.fragments[2].steps
    )
    assert fragment.completed_operation_ids == (0,)
    assert fragment.retained_atoms == ()
    assert replay(AtomProgram(fragment.steps), entry, device) == final
