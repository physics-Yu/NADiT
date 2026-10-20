"""Reuse observes result availability and actual positions after local controls."""

from dataclasses import replace
from math import pi
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator
from test_reuse_compiler import _dag, _strategy
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler import (
    AtomLayout,
    CZPulse,
    Device,
    GateLayer,
    Load,
    PhysicalDAG,
    PlacementPlan,
    ResultRef,
    Rotation,
    SLMHolder,
    apply_step,
    compile_physical,
    load_device,
)

MODULE = Path(__file__).parents[1]


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


@pytest.mark.parametrize("bit", [0, 1])
def test_compiled_result_enables_reuse_while_actions_wait_for_feedback(
    device: Device, bit: int
) -> None:
    assert device.readout is not None
    device = replace(device, readout=replace(device.readout, latency_us=100_000))
    dag = _dag("c = measure q[0]; cz q[1],q[2]; if(c) { rz(0.2) q[1]; } cz q[1],q[2];")
    entry = _entry(device)
    backend = RecordingBackend(bit)
    strategy = _strategy(device)
    observations: list[tuple[GateLayer, tuple[int, ...], tuple[ResultRef, ...]]] = []

    class ObservedPlacer:
        def place(
            self,
            dag: PhysicalDAG,
            layer: GateLayer,
            layout: AtomLayout,
            device: Device,
            *,
            reuse_qubits: tuple[int, ...] = (),
        ) -> PlacementPlan:
            if dag.nodes[layer.operation_ids[0]].name == "cz":
                observations.append((layer, reuse_qubits, tuple(backend.calls)))
            return strategy.placer.place(
                dag, layer, layout, device, reuse_qubits=reuse_qubits
            )

    result = compile_physical(
        dag,
        device,
        strategy=replace(strategy, placer=ObservedPlacer()),
        entry_state=entry,
        measurement_backend=backend,
    )

    _audit(dag, result, device, entry)
    measurement = result.measurements[0]
    first_layer, reuse_qubits, calls = observations[0]
    first_cz = result.operations[first_layer.operation_ids[0]]
    assert calls == (measurement.result,)
    assert first_cz.end_us < measurement.available_at_us
    assert reuse_qubits == (1, 2)
    assert backend.calls == [measurement.result]
    assert len(observations) == 2
    dependent = next(node for node in dag.nodes if node.condition is not None)
    assert result.operations[dependent.id].start_us >= measurement.available_at_us
    for qubit in reuse_qubits:
        assert (
            sum(
                atom == entry.qubit_to_atom[qubit]
                for step in result.program.steps
                for action in step.actions
                if isinstance(action, Load)
                for atom, _, _ in action.bindings
            )
            == 2
        )


@pytest.mark.parametrize("bit", [0, 1])
@pytest.mark.parametrize("capacity", [1, 2])
def test_independent_cz_can_run_before_the_previewed_conditional_layer(
    device: Device, bit: int, capacity: int
) -> None:
    assert device.readout is not None
    device = replace(device, readout=replace(device.readout, latency_us=100_000))
    dag = _dag(
        "c = measure q[4]; cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "if(c) { cz q[0],q[1]; } cz q[2],q[3];",
        5,
    )
    entry = _entry(device, 5)
    backend = RecordingBackend(bit)
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(device, capacity=capacity),
        entry_state=entry,
        measurement_backend=backend,
    )

    _audit(dag, result, device, entry)
    measurement = result.measurements[0]
    independent = dag.nodes[-1]
    conditional = next(node for node in dag.nodes if node.condition is not None)
    assert result.operations[independent.id].end_us < measurement.available_at_us
    assert result.operations[conditional.id].start_us >= measurement.available_at_us
    assert result.operations[conditional.id].status == ("emitted" if bit else "skipped")
    assert backend.calls == [measurement.result]


@pytest.mark.parametrize("bit", [0, 1])
def test_conditional_phase_between_cz_layers_is_committed_once(
    device: Device, bit: int
) -> None:
    dag = _dag(
        "gphase(pi/7); c = measure q[0]; barrier q; "
        "if(c) { cz q[0],q[1]; cz q[2],q[3]; p(pi/3) q[0]; "
        "cz q[0],q[2]; cz q[1],q[3]; } "
        "else { cz q[0],q[2]; cz q[1],q[3]; p(-pi/5) q[1]; "
        "cz q[0],q[2]; cz q[1],q[3]; }"
    )
    entry = _entry(device)
    backend = RecordingBackend(bit)
    reused = compile_physical(
        dag,
        device,
        strategy=_strategy(device),
        entry_state=entry,
        measurement_backend=backend,
    )
    baseline_backend = RecordingBackend(bit)
    baseline = compile_physical(
        dag,
        device,
        strategy=_strategy(device, reuse=False),
        entry_state=entry,
        measurement_backend=baseline_backend,
    )
    _audit(dag, reused, device, entry)
    _audit(dag, baseline, device, entry)

    assert [(item.operation_id, item.status) for item in reused.operations] == [
        (item.operation_id, item.status) for item in baseline.operations
    ]
    assert reused.program.global_phase == baseline.program.global_phase
    assert backend.calls == baseline_backend.calls == [reused.measurements[0].result]
    actual = QuantumCircuit(4)
    actual.global_phase = reused.program.global_phase
    wires = {atom: q for q, atom in enumerate(entry.qubit_to_atom)}
    for step in reused.program.steps:
        for action in step.actions:
            if isinstance(action, Rotation):
                for atom in action.atoms:
                    getattr(actual, "r" + action.axis.lower())(
                        action.angle, wires[atom]
                    )
            elif isinstance(action, CZPulse):
                for operation_id in action.source_ids:
                    actual.cz(*dag.nodes[operation_id].qubits)
    expected = QuantumCircuit(4)
    expected.global_phase = pi / 7
    if bit:
        expected.cz(0, 1)
        expected.cz(2, 3)
        expected.p(pi / 3, 0)
    else:
        expected.cz(0, 2)
        expected.cz(1, 3)
        expected.p(-pi / 5, 1)
    expected.cz(0, 2)
    expected.cz(1, 3)
    assert np.allclose(Operator(actual).data, Operator(expected).data)


def test_local_control_relocation_recomputes_actual_gate_anchors(
    device: Device,
) -> None:
    device = replace(
        device,
        zones=tuple(
            replace(
                zone, operations=tuple(op for op in zone.operations if op != "rotate")
            )
            if zone.id == "entanglement"
            else zone
            for zone in device.zones
        ),
    )
    dag = _dag("cz q[0],q[1]; cz q[2],q[3]; rx(0.3) q[0]; cz q[0],q[1]; cz q[2],q[3];")
    entry = _entry(device)
    strategy = _strategy(device)
    observed: list[tuple[AtomLayout, tuple[int, ...]]] = []

    class ObservedPlacer:
        def place(
            self,
            dag: PhysicalDAG,
            layer: GateLayer,
            layout: AtomLayout,
            device: Device,
            *,
            reuse_qubits: tuple[int, ...] = (),
        ) -> PlacementPlan:
            if dag.nodes[layer.operation_ids[0]].name == "cz":
                observed.append((layout, reuse_qubits))
            return strategy.placer.place(
                dag, layer, layout, device, reuse_qubits=reuse_qubits
            )

    result = compile_physical(
        dag,
        device,
        strategy=replace(strategy, placer=ObservedPlacer()),
        entry_state=entry,
    )
    _audit(dag, result, device, entry)

    assert observed[0][1] == (0, 1, 2, 3)
    second_entry, _ = observed[1]
    relocated = second_entry.atom(entry.qubit_to_atom[0])
    assert isinstance(relocated.holder, SLMHolder)
    assert relocated.holder.site_id.startswith("storage_")
    for q in (1, 2, 3):
        holder = second_entry.atom(entry.qubit_to_atom[q]).holder
        assert isinstance(holder, SLMHolder)
        assert holder.site_id.startswith("gate_")
    layout = entry
    after_rotation = False
    reloads: set[int] = set()
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, Rotation):
                assert device.zones_at(layout.atom(relocated.id).position, "rotate")
                assert not device.zones_at(layout.atom(relocated.id).position, "cz")
                after_rotation = True
            elif after_rotation and isinstance(action, Load):
                reloads.update(atom for atom, _, _ in action.bindings)
            elif after_rotation and isinstance(action, CZPulse):
                assert relocated.id in reloads
                assert device.zones_at(layout.atom(relocated.id).position, "cz")
                after_rotation = False
        layout = apply_step(layout, step, device)
    assert len(observed) == 2
