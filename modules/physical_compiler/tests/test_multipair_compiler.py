"""Multi-pair physical compilation with independent interaction and replay checks."""

from collections import Counter
from dataclasses import replace
from itertools import combinations
from math import hypot, pi
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator

from nadit_physical_compiler import (
    AODHolder,
    AtomLayout,
    AtomState,
    CompilationResult,
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
    apply_step,
    check_load,
    check_move,
    check_store,
    compile_physical,
    load_device,
    load_qasm,
    loads_qasm,
    replay,
    validate_layout,
)
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import SequentialTransport

MODULE = Path(__file__).parents[1]
PAIRS = (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right"))


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


def _entry(device: Device, count: int = 4) -> AtomLayout:
    sites = [site for site in device.slm_sites if site.id.startswith("storage_")]
    atoms = tuple(
        AtomState(7 + q * 3, site.position, SLMHolder(site.id))
        for q, site in enumerate(sites[:count])
    )
    return AtomLayout(atoms, qubit_to_atom=tuple(atom.id for atom in atoms))


def _strategy(device: Device, *, capacity: int = 2) -> SearchSmarterStrategy:
    catalogue = GatePairCatalog.from_device(device, "entanglement", PAIRS[:capacity])
    return SearchSmarterStrategy(
        ReadyScheduler(max_cz_gates=catalogue.capacity),
        MultiPairPlacer("transport", catalogue),
        SequentialTransport("transport"),
        WholeLayerEmitter(catalogue.coverage),
    )


def _qasm(body: str, count: int = 4) -> PhysicalDAG:
    return loads_qasm(
        f'OPENQASM 3.0; include "stdgates.inc"; qubit[{count}] q; bit c; {body}'
    )


def _compile(
    dag: PhysicalDAG, device: Device, entry: AtomLayout, bit: int = 1, capacity: int = 2
) -> CompilationResult:
    overrides = {
        reference: bit for node in dag.nodes if (reference := node.result) is not None
    }
    return compile_physical(
        dag,
        device,
        strategy=_strategy(device, capacity=capacity),
        entry_state=entry,
        measurement_backend=FakeBackend(17, overrides),
    )


def _audit(
    dag: PhysicalDAG, result: CompilationResult, device: Device, entry: AtomLayout
) -> QuantumCircuit:
    """Recover the selected unitary factors from actual illuminated geometry."""
    layout = entry
    wires = {atom: q for q, atom in enumerate(entry.qubit_to_atom)}
    unitary = QuantumCircuit(dag.num_qubits)
    unitary.global_phase = result.program.global_phase
    coverage: Counter[int] = Counter()
    for start, step in result.program.timeline():
        pulses = [action for action in step.actions if isinstance(action, CZPulse)]
        assert len(pulses) <= 1
        for action in step.actions:
            coverage.update(action.source_ids)
            if isinstance(action, Load):
                assert (
                    len(action.bindings)
                    == len(action.grid.rows)
                    == len(action.grid.columns)
                    == 1
                )
                checked = check_load(
                    device,
                    layout,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
                assert checked.ok, checked.message
            elif isinstance(action, Move):
                assert len(action.targets) == 1
                checked = check_move(device, layout, action.grid, dict(action.targets))
                assert checked.ok, checked.message
                atom, target = action.targets[0]
                source = layout.atom(atom).position
                assert action.duration_us == pytest.approx(
                    device.timing.move_duration(
                        hypot(target.x - source.x, target.y - source.y)
                    )
                )
            elif isinstance(action, Store):
                assert len(action.bindings) == 1
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
                actual = {
                    frozenset((a.id, b.id))
                    for a, b in combinations(illuminated, 2)
                    if hypot(a.position.x - b.position.x, a.position.y - b.position.y)
                    <= device.cz.radius_um
                }
                intended = {
                    frozenset(entry.qubit_to_atom[q] for q in dag.nodes[op].qubits)
                    for op in action.source_ids
                }
                assert actual == intended
                assert {atom.id for atom in illuminated} == set().union(*intended)
                for first, second in actual:
                    unitary.cz(wires[first], wires[second])
                assert action.duration_us == device.cz.duration_us
                for op in action.source_ids:
                    assert result.operations[op].start_us == start
                    assert result.operations[op].end_us == start + action.duration_us
            elif isinstance(action, Measure):
                assert action.retain_atoms
                for atom, reference in action.results:
                    assert isinstance(layout.atom(atom).holder, AODHolder)
                    assert device.zones_at(layout.atom(atom).position, "measure")
                    assert reference == dag.nodes[action.source_ids[0]].result
            elif isinstance(action, Rotation):
                for atom in action.atoms:
                    assert device.zones_at(layout.atom(atom).position, "rotate")
                    getattr(unitary, "r" + action.axis.lower())(
                        action.angle, wires[atom]
                    )
        layout = apply_step(layout, step, device)
        validate_layout(device, layout)
    assert layout == result.exit_state == replay(result.program, entry, device)
    assert not layout.aod_grids
    for record in result.operations:
        assert coverage[record.operation_id] == (1 if record.status == "emitted" else 0)
    for before, after in dag.edges:
        assert result.operations[before].end_us <= result.operations[after].start_us
    outcomes = {item.result: item for item in result.measurements}
    for node, record in zip(dag.nodes, result.operations, strict=True):
        if node.condition is not None:
            observed = outcomes[node.condition.result]
            assert (record.status == "skipped") == (
                node.condition.value != observed.value
            )
            assert record.start_us >= observed.available_at_us
    return unitary


@pytest.mark.parametrize("bit", [0, 1])
def test_feedback_and_partner_changes_compile_to_shared_pulses(
    device: Device, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)
    result = _compile(dag, device, entry, bit)
    actual = _audit(dag, result, device, entry)
    pulses = [
        action
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    ]
    assert [len(pulse.source_ids) for pulse in pulses] == [2] * (2 + bit)
    assert result.exit_state == entry
    assert len(result.measurements) == 1
    assert result.measurements[0].value == bit
    assert any(
        isinstance(action, Wait)
        for step in result.program.steps
        for action in step.actions
    )
    expected = QuantumCircuit(4)
    expected.h(0)
    expected.h(2)
    expected.cz(0, 1)
    expected.cz(2, 3)
    expected.cz(0, 2)
    expected.cz(1, 3)
    if bit:
        expected.cz(0, 1)
        expected.cz(2, 3)
        expected.p(pi / 3, 1)
    else:
        expected.rx(pi, 1)
    assert np.allclose(Operator(actual).data, Operator(expected).data)


@pytest.mark.parametrize("capacity,expected", [(1, [1, 1, 1]), (2, [2, 1])])
def test_ready_gates_split_at_fixed_pair_capacity(
    device: Device, capacity: int, expected: list[int]
) -> None:
    dag = _qasm("cz q[0],q[1]; cz q[2],q[3]; cz q[4],q[5];", 6)
    entry = _entry(device, 6)
    result = _compile(dag, device, entry, capacity=capacity)
    _audit(dag, result, device, entry)
    assert [
        len(action.source_ids)
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    ] == expected
    assert result.exit_state == entry


def test_spectators_in_unused_gate_sites_leave_before_the_pulse(device: Device) -> None:
    entry = _entry(device)
    spectators = tuple(
        AtomState(atom, device.site(site).position, SLMHolder(site))
        for atom, site in ((100, "gate_1_left"), (101, "gate_1_right"))
    )
    entry = replace(entry, atoms=entry.atoms + spectators)
    dag = _qasm("cz q[0],q[1];")
    result = _compile(dag, device, entry)
    _audit(dag, result, device, entry)
    storage = next(zone.bounds for zone in device.zones if zone.id == "storage")
    assert all(storage.contains(atom.position) for atom in result.exit_state.atoms)


def test_fake_result_is_sampled_once_and_reused_for_multiple_cz(
    device: Device,
) -> None:
    class Backend:
        def __init__(self) -> None:
            self.calls: list[ResultRef] = []

        def measure(self, reference: ResultRef) -> int:
            self.calls.append(reference)
            return 1

    dag = _qasm("h q[0]; c = measure q[0]; if(c) { cz q[0],q[1]; cz q[2],q[3]; }")
    backend = Backend()
    entry = _entry(device)
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(device),
        entry_state=entry,
        measurement_backend=backend,
    )
    _audit(dag, result, device, entry)
    reference = next(node.result for node in dag.nodes if node.name == "measure")
    assert backend.calls == [reference]
    assert result.program.to_text() == _compile(dag, device, entry).program.to_text()


@pytest.mark.parametrize("bit", [0, 1])
def test_qasm2_multi_cz_feedback(device: Device, bit: int) -> None:
    dag = loads_qasm(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[4]; creg c[1]; '
        "cz q[0],q[1]; cz q[2],q[3]; measure q[0] -> c[0]; "
        "if(c==1) cz q[0],q[2]; if(c==1) cz q[1],q[3];"
    )
    entry = _entry(device)
    result = _compile(dag, device, entry, bit)
    _audit(dag, result, device, entry)
    assert (
        len(
            [
                layer
                for layer in result.layers
                if dag.nodes[layer.operation_ids[0]].name == "cz"
            ]
        )
        == 1 + bit
    )


def test_full_transport_checks_are_not_called_during_compilation(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nadit_physical_compiler import motion

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "Reference transport checks must stay outside compilation."
        )

    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)
    result = _compile(_qasm("cz q[0],q[1]; cz q[2],q[3];"), device, _entry(device))
    assert len(result.layers) == 1
