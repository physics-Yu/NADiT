"""Local control must not turn every occupied gate site into a retained anchor."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_ids_compiler import _strategy
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler.atom_ir import CZPulse, Load, Rotation
from nadit_physical_compiler.compiler import CompilationResult, compile_physical
from nadit_physical_compiler.contracts import GateLayer, PlacementPlan
from nadit_physical_compiler.device import Device, load_device
from nadit_physical_compiler.frontend import loads_qasm
from nadit_physical_compiler.physical_ir import PhysicalDAG, ResultRef
from nadit_physical_compiler.placement import HeuristicPlacer
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.strategies import SearchSmarterStrategy

MODULE = Path(__file__).parents[1]
type Mode = Literal["fixed", "ids"]


@pytest.fixture
def gate_local_device() -> Device:
    device = load_device(MODULE / "configs/multipair_flexible.json")
    return replace(
        device,
        zones=tuple(
            replace(
                zone,
                operations=tuple(name for name in zone.operations if name != "rotate"),
            )
            if zone.id == "storage"
            else zone
            for zone in device.zones
        ),
    )


def _dag(body: str) -> PhysicalDAG:
    return loads_qasm(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; bit c; ' + body
    )


def _retention_strategy(device: Device, mode: Mode) -> SearchSmarterStrategy:
    strategy = _strategy(device, mode)
    if mode == "ids":
        placer = strategy.placer
        assert isinstance(placer, HeuristicPlacer)
        # Exercise actual IDS retention, not a candidate that is sent to storage.
        strategy = replace(
            strategy,
            placer=replace(placer, config=replace(placer.config, reuse_level=100)),
        )
    return strategy


def _assert_shared_anchor_stays(
    result: CompilationResult, entry: AtomLayout, device: Device
) -> None:
    layout = entry
    anchor: AtomState | None = None
    shared_id = entry.qubit_to_atom[0]
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, CZPulse):
                if anchor is None:
                    anchor = layout.atom(shared_id)
                else:
                    assert layout.atom(shared_id) == anchor
                    return
            elif anchor is not None and isinstance(action, Load):
                assert shared_id not in {atom for atom, _, _ in action.bindings}
        layout = apply_step(layout, step, device)
    pytest.fail("The regression must execute both CZ pulses.")


def _audit_rotations_and_pulses(
    dag: PhysicalDAG,
    result: CompilationResult,
    device: Device,
    entry: AtomLayout,
) -> list[AtomLayout]:
    _audit(dag, result, device, entry)
    layout = entry
    rotation_layouts = []
    emitted_cz: list[int] = []
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, Rotation):
                rotation_layouts.append(layout)
                for atom_id in action.atoms:
                    zones = device.zones_at(layout.atom(atom_id).position, "rotate")
                    assert [zone.id for zone in zones] == ["entanglement"]
            elif isinstance(action, CZPulse):
                emitted_cz.extend(action.source_ids)
        layout = apply_step(layout, step, device)
    assert sorted(emitted_cz) == [
        node.id
        for node, record in zip(dag.nodes, result.operations, strict=True)
        if node.name == "cz" and record.status == "emitted"
    ]
    assert all(
        isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id.startswith("storage_")
        for atom in result.exit_state.atoms
    )
    return rotation_layouts


@pytest.mark.parametrize("mode", ["fixed", "ids"])
def test_local_rotation_at_another_pair_does_not_create_a_false_anchor(
    gate_local_device: Device, mode: Mode
) -> None:
    dag = _dag("cz q[0],q[1]; rx(0.3) q[2]; cz q[0],q[2];")
    entry = _entry(gate_local_device)
    backend = RecordingBackend(1)

    result = compile_physical(
        dag,
        gate_local_device,
        strategy=_retention_strategy(gate_local_device, mode),
        entry_state=entry,
        measurement_backend=backend,
    )

    rotations = _audit_rotations_and_pulses(dag, result, gate_local_device, entry)
    _assert_shared_anchor_stays(result, entry, gate_local_device)
    assert len(rotations) == 1
    assert backend.calls == []


@pytest.mark.parametrize("mode", ["fixed", "ids"])
@pytest.mark.parametrize("bit", [0, 1])
def test_conditional_local_control_uses_only_the_emitted_measurement(
    gate_local_device: Device, mode: Mode, bit: int
) -> None:
    dag = _dag(
        "c = measure q[3]; barrier q; cz q[0],q[1]; "
        "if(c) { rx(0.3) q[2]; } cz q[0],q[2];"
    )
    entry = _entry(gate_local_device)
    backend = RecordingBackend(bit)
    strategy = _retention_strategy(gate_local_device, mode)
    observations: list[tuple[str, tuple[ResultRef, ...]]] = []

    class ObservedPlacer:
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
            observations.append(
                (dag.nodes[layer.operation_ids[0]].name, tuple(backend.calls))
            )
            return strategy.placer.place(
                dag,
                layer,
                layout,
                device,
                reuse_qubits=reuse_qubits,
                following=following,
                retained_atoms=retained_atoms,
            )

    result = compile_physical(
        dag,
        gate_local_device,
        strategy=replace(strategy, placer=ObservedPlacer()),
        entry_state=entry,
        measurement_backend=backend,
    )

    rotations = _audit_rotations_and_pulses(dag, result, gate_local_device, entry)
    _assert_shared_anchor_stays(result, entry, gate_local_device)
    assert observations[0] == ("measure", ())
    assert backend.calls == [result.measurements[0].result]
    assert result.measurements[0].value == bit
    assert len(rotations) == bit
    conditional = next(node for node in dag.nodes if node.condition is not None)
    assert (result.operations[conditional.id].status == "emitted") == bool(bit)


@pytest.mark.parametrize("mode", ["fixed", "ids"])
def test_all_gate_sites_used_by_local_control_can_be_repaired_for_new_pairs(
    gate_local_device: Device, mode: Mode
) -> None:
    dag = _dag("rx(0.3) q; cz q[0],q[2]; cz q[1],q[3];")
    entry = _entry(gate_local_device)

    result = compile_physical(
        dag,
        gate_local_device,
        strategy=_retention_strategy(gate_local_device, mode),
        entry_state=entry,
    )

    rotations = _audit_rotations_and_pulses(dag, result, gate_local_device, entry)
    assert len(rotations) == 4
    assert all(
        isinstance(atom.holder, SLMHolder) and atom.holder.site_id.startswith("gate_")
        for atom in rotations[0].atoms
    )
