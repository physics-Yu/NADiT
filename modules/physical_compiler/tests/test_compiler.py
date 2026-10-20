"""One-pair compilation, physical actions, and compile-time feedback."""

from dataclasses import replace
from math import hypot, pi
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator

from nadit_physical_compiler import (
    AtomLayout,
    AtomState,
    CZPulse,
    Device,
    FakeBackend,
    Load,
    Measure,
    Move,
    PhysicalDAG,
    Region,
    ResultRef,
    Rotation,
    SLMHolder,
    Store,
    Wait,
    check_load,
    check_move,
    check_store,
    load_device,
    loads_qasm,
    validate_layout,
)
from nadit_physical_compiler.compiler import CompilationResult, compile_physical
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies.search_smarter import SearchSmarterStrategy
from nadit_physical_compiler.transport import PairTransport


def _source(body: str) -> str:
    return f'OPENQASM 3.0; include "stdgates.inc"; qubit[2] q; bit c; {body}'


@pytest.fixture
def device() -> Device:
    return load_device(Path(__file__).parents[1] / "configs" / "one_pair_flexible.json")


@pytest.fixture
def entry(device: Device) -> AtomLayout:
    return AtomLayout(
        tuple(
            AtomState(atom, device.site(site).position, SLMHolder(site))
            for atom, site in ((7, "storage_r0_c0"), (11, "storage_r0_c1"))
        ),
        qubit_to_atom=(7, 11),
    )


def _strategy() -> SearchSmarterStrategy:
    return SearchSmarterStrategy(
        ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
    )


def _compile(
    source: str, device: Device, entry: AtomLayout, *, bit: int = 1
) -> tuple[PhysicalDAG, CompilationResult]:
    dag = loads_qasm(source)
    outcomes = {
        reference: bit for node in dag.nodes if (reference := node.result) is not None
    }
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(),
        entry_state=entry,
        measurement_backend=FakeBackend(overrides=outcomes),
    )
    return dag, result


def _check_actions(
    result: CompilationResult, dag: PhysicalDAG, device: Device, entry: AtomLayout
) -> None:
    layout = entry
    for _, step in result.program.timeline():
        for action in step.actions:
            if isinstance(action, Load):
                checked = check_load(
                    device,
                    layout,
                    action.grid,
                    {atom: (row, col) for atom, row, col in action.bindings},
                )
                assert checked.ok, checked.message
            elif isinstance(action, Move):
                checked = check_move(device, layout, action.grid, dict(action.targets))
                assert checked.ok, checked.message
                distance = max(
                    hypot(
                        position.x - layout.atom(atom).position.x,
                        position.y - layout.atom(atom).position.y,
                    )
                    for atom, position in action.targets
                )
                assert action.duration_us == pytest.approx(
                    device.timing.move_duration(distance)
                )
            elif isinstance(action, Store):
                checked = check_store(
                    device, layout, action.grid, dict(action.bindings)
                )
                assert checked.ok, checked.message
            elif isinstance(action, CZPulse):
                assert device.cz is not None
                assert isinstance(action.coverage, Region)
                illuminated = [
                    atom
                    for atom in layout.atoms
                    if action.coverage.contains(atom.position)
                ]
                assert len(illuminated) == 2
                a, b = illuminated
                assert (
                    hypot(a.position.x - b.position.x, a.position.y - b.position.y)
                    <= device.cz.radius_um
                )
                assert all(device.zones_at(atom.position, "cz") for atom in illuminated)
                intended = {
                    entry.qubit_to_atom[q]
                    for op in action.source_ids
                    for q in dag.nodes[op].qubits
                }
                assert {atom.id for atom in illuminated} == intended
            elif isinstance(action, Rotation):
                assert all(
                    device.zones_at(layout.atom(atom).position, "rotate")
                    for atom in action.atoms
                )
            elif isinstance(action, Measure):
                assert all(
                    device.zones_at(layout.atom(atom).position, "measure")
                    for atom, _ in action.results
                )
                assert action.retain_atoms
        layout = apply_step(layout, step, device)
        validate_layout(device, layout)
    assert layout == result.exit_state
    assert replay(result.program, entry, device) == result.exit_state


@pytest.mark.parametrize("bit", [0, 1])
def test_complete_pair_feedback_path(
    device: Device, entry: AtomLayout, bit: int
) -> None:
    dag, result = _compile(
        _source(
            "reset q; ry(pi/2) q[0]; cz q[0],q[1]; c = measure q[0]; "
            "if(c) { p(pi/3) q[1]; } else { rx(pi) q[1]; }"
        ),
        device,
        entry,
        bit=bit,
    )
    _check_actions(result, dag, device, entry)
    actions = [action for step in result.program.steps for action in step.actions]
    assert all(
        any(isinstance(action, kind) for action in actions)
        for kind in (Load, Move, Store, CZPulse, Measure, Rotation, Wait)
    )
    assert len(result.measurements) == 1
    measurement = result.measurements[0]
    assert measurement.result == ResultRef(4)
    assert measurement.value == bit
    for node, record in zip(dag.nodes, result.operations, strict=True):
        assert record.operation_id == node.id
        if node.condition is not None:
            assert (record.status == "skipped") == (node.condition.value != bit)
            assert record.start_us >= measurement.available_at_us
    for before, after in dag.edges:
        assert result.operations[before].end_us <= result.operations[after].start_us
    emitted_ids = {op for action in actions for op in action.source_ids}
    assert emitted_ids == {
        record.operation_id
        for record in result.operations
        if record.status == "emitted"
    }
    assert not result.exit_state.aod_grids


@pytest.mark.parametrize("bit", [0, 1])
def test_selected_action_unitary_preserves_conditional_phase(
    device: Device, entry: AtomLayout, bit: int
) -> None:
    _, result = _compile(
        _source(
            "h q[0]; cx q[0],q[1]; c = measure q[0]; "
            "if(c) { p(pi/3) q[1]; } else { rx(pi) q[1]; }"
        ),
        device,
        entry,
        bit=bit,
    )
    actual = QuantumCircuit(2)
    actual.global_phase = result.program.global_phase
    wires = {atom: q for q, atom in enumerate(entry.qubit_to_atom)}
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, Rotation):
                for atom in action.atoms:
                    getattr(actual, "r" + action.axis.lower())(
                        action.angle, wires[atom]
                    )
            elif isinstance(action, CZPulse):
                actual.cz(0, 1)
    expected = QuantumCircuit(2)
    expected.h(0)
    expected.cx(0, 1)
    if bit:
        expected.p(pi / 3, 1)
    else:
        expected.rx(pi, 1)
    assert np.allclose(Operator(actual).data, Operator(expected).data)


def test_backend_called_once_per_source_measurement(
    device: Device, entry: AtomLayout
) -> None:
    class CountingBackend:
        def __init__(self) -> None:
            self.calls: list[ResultRef] = []

        def measure(self, result: ResultRef) -> int:
            self.calls.append(result)
            return 1

    dag = loads_qasm(
        _source(
            "h q[0]; c = measure q[0]; if(c) { rx(0.2) q[1]; rz(0.3) q[1]; } "
            "c = measure q[0]; if(c) { ry(0.4) q[1]; }"
        )
    )
    backend = CountingBackend()
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(),
        entry_state=entry,
        measurement_backend=backend,
    )
    assert backend.calls == [
        node.result for node in dag.nodes if node.name == "measure"
    ]
    assert len(result.measurements) == len(set(backend.calls)) == 2
    assert (
        result.program.to_text()
        == compile_physical(
            dag,
            device,
            strategy=_strategy(),
            entry_state=entry,
            measurement_backend=FakeBackend(overrides=dict.fromkeys(backend.calls, 1)),
        ).program.to_text()
    )


def test_independent_work_fills_feedback_latency(
    device: Device, entry: AtomLayout
) -> None:
    assert device.local is not None
    assert device.readout is not None
    device = replace(
        device,
        local=replace(device.local, rotation_us=300),
        readout=replace(device.readout, latency_us=500),
    )
    dag, result = _compile(
        _source("c = measure q[0]; rx(0.2) q[1]; if(c) { ry(0.3) q[0]; }"),
        device,
        entry,
    )
    assert result.operations[1].start_us < result.measurements[0].available_at_us
    assert result.operations[2].start_us >= result.measurements[0].available_at_us
    assert not any(
        isinstance(action, Wait)
        for step in result.program.steps
        for action in step.actions
    )
    _check_actions(result, dag, device, entry)


def test_two_ready_rotations_share_a_step_and_barrier_is_metadata(
    device: Device, entry: AtomLayout
) -> None:
    _, result = _compile(
        _source("rx(0.2) q[0]; rx(0.4) q[1]; barrier q; rz(0.1) q[0];"),
        device,
        entry,
    )
    assert len(result.program.steps[0].actions) == 2
    assert result.operations[0].start_us == result.operations[1].start_us == 0
    assert result.operations[2].status == "metadata"
    assert result.program.duration_us == 2


def test_compilation_uses_constructed_actions_without_reference_checks(
    device: Device, entry: AtomLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nadit_physical_compiler import motion

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Full transport checks belong to tests/debugging.")

    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)
    _, result = _compile(
        _source("cz q[0],q[1]; c = measure q[0]; if(c) { rx(0.2) q[1]; }"),
        device,
        entry,
    )
    assert result.program.steps


def test_empty_circuit_and_global_phase(device: Device, entry: AtomLayout) -> None:
    _, result = _compile(_source("gphase(pi/3);"), device, entry)
    assert not result.program.steps
    assert result.program.global_phase == pytest.approx(pi / 3)
    assert result.exit_state is entry


def test_non_bit_backend_result_is_reported(device: Device, entry: AtomLayout) -> None:
    with pytest.raises(ValueError, match="non-bit"):
        _compile(_source("c = measure q[0];"), device, entry, bit=2)


def test_final_result_latency_does_not_add_an_unused_action_wait(
    device: Device, entry: AtomLayout
) -> None:
    assert device.readout is not None
    device = replace(device, readout=replace(device.readout, latency_us=1000))
    _, result = _compile(_source("c = measure q[0];"), device, entry)
    assert result.duration_us == result.measurements[0].available_at_us
    assert result.duration_us > result.program.duration_us
    assert not any(
        isinstance(action, Wait)
        for step in result.program.steps
        for action in step.actions
    )


def test_entry_requires_complete_qubit_binding(
    device: Device, entry: AtomLayout
) -> None:
    with pytest.raises(ValueError, match="qubit"):
        _compile(_source("cz q[0],q[1];"), device, replace(entry, qubit_to_atom=()))
