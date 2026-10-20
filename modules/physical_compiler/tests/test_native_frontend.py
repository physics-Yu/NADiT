"""Native lowering, source identity, and feedback preservation at the frontend."""

from math import pi
from pathlib import Path

import numpy as np
import pytest
from qiskit import QuantumCircuit, qasm3
from qiskit.circuit.library import GlobalPhaseGate
from qiskit.quantum_info import Operator
from qiskit.transpiler import PassManager

from nadit_physical_compiler import (
    Condition,
    FakeBackend,
    PhysicalDAG,
    ResultRef,
    execute,
)
from nadit_physical_compiler.frontend import (
    DEFAULT_GATE_SET,
    GateSet,
    load_qasm,
    loads_qasm,
)
from nadit_physical_compiler.frontend import qasm as qasm_frontend
from nadit_physical_compiler.frontend.dag import build_physical_dag
from nadit_physical_compiler.frontend.native import NativeCircuit


def _source(body: str, qubits: int = 3) -> str:
    return f'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[{qubits}] q;\nbit c;\n{body}'


def _unitary(dag: PhysicalDAG, *, branch: int | None = None) -> Operator:
    circuit = QuantumCircuit(dag.num_qubits)
    circuit.global_phase = dag.global_phase
    for node in dag.nodes:
        if node.name in ("measure", "reset", "barrier"):
            continue
        if node.condition is not None and node.condition.value != branch:
            continue
        if node.name == "global_phase":
            circuit.append(GlobalPhaseGate(node.params[0]), [])
        else:
            getattr(circuit, node.name)(*node.params, *node.qubits)
    return Operator(circuit)


@pytest.mark.parametrize(
    "body",
    [
        "h q[0];",
        "h q[0]; cx q[0],q[1];",
        "cp(0.37) q[1],q[0];",
        "ccx q[2],q[0],q[1];",
        "swap q[1],q[2];",
        "U(0.2,0.3,0.4) q[0];",
        "rx(pi/3) q[2]; ry(-0.7) q[0]; rz(0.4) q[1];",
        "p(0.5) q[0];",
        "gphase(pi/4); h q[0];",
        "gate pair(t) a,b { h a; cp(t) b,a; } pair(pi/7) q[2],q[0];",
    ],
)
def test_native_lowering_preserves_the_complete_unitary(body: str) -> None:
    source = _source(body)
    dag = loads_qasm(source)

    assert {node.name for node in dag.nodes} <= set(DEFAULT_GATE_SET.names)
    assert np.allclose(_unitary(dag).data, Operator(qasm3.loads(source)).data)
    assert [node.id for node in dag.nodes] == list(range(len(dag.nodes)))


def test_native_lowering_happens_before_physical_dag_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[NativeCircuit] = []

    def capture(native: NativeCircuit) -> PhysicalDAG:
        seen.append(native)
        assert {item.operation.name for item in native.circuit.data} <= set(
            DEFAULT_GATE_SET.names
        )
        return build_physical_dag(native)

    monkeypatch.setattr(qasm_frontend, "build_physical_dag", capture)
    loads_qasm(_source("h q[0]; cx q[0],q[1];"))

    assert len(seen) == 1
    assert set(seen[0].source_ids) == {0, 1}


@pytest.mark.parametrize("branch", [0, 1])
def test_conditional_decomposition_keeps_phase_and_every_child_guarded(
    branch: int,
) -> None:
    source = _source(
        "gphase(pi/8); c = measure q[0]; "
        "if (c) { gphase(pi/7); p(pi/3) q[1]; } "
        "else { x q[2]; }"
    )
    dag = loads_qasm(source)
    guarded = [node for node in dag.nodes if node.condition is not None]
    phases = [node for node in guarded if node.name == "global_phase"]
    expected = QuantumCircuit(3)
    expected.global_phase = pi / 8
    if branch:
        expected.global_phase += pi / 7
        expected.p(pi / 3, 1)
    else:
        expected.x(2)

    assert dag.global_phase == pytest.approx(pi / 8)
    assert phases
    assert all(
        node.condition is not None and node.condition.result == ResultRef(0)
        for node in guarded
    )
    assert all((0, node.id) in dag.edges for node in guarded)
    assert np.allclose(_unitary(dag, branch=branch).data, Operator(expected).data)


def test_source_measurements_and_seeded_results_survive_gate_set_changes() -> None:
    source = _source(
        "h q[0]; c = measure q[0]; if (c) { p(pi/3) q[1]; } "
        "cx q[1],q[2]; c = measure q[2]; if (!c) { x q[0]; }"
    )
    circuits = [
        loads_qasm(source, gate_set=gate_set)
        for gate_set in (
            DEFAULT_GATE_SET,
            GateSet(("u", "cz")),
            GateSet(("h", "cx", "p", "x", "z")),
        )
    ]
    references = [
        tuple(node.result for node in dag.nodes if node.name == "measure")
        for dag in circuits
    ]
    outcomes = [
        {
            event.result: event.value
            for event in execute(dag, FakeBackend(17)).measurements
        }
        for dag in circuits
    ]

    assert references == [(ResultRef(1), ResultRef(4))] * 3
    assert outcomes[0] == outcomes[1] == outcomes[2]
    assert len({len(dag.nodes) for dag in circuits}) > 1
    for dag in circuits:
        producers = {
            node.result: node.id for node in dag.nodes if node.name == "measure"
        }
        for node in dag.nodes:
            if node.condition is not None:
                assert (producers[node.condition.result], node.id) in dag.edges
                assert node.condition.result == ResultRef(
                    1 if node.source_id == 2 else 4
                )


def test_controls_and_wire_dependencies_survive_native_expansion() -> None:
    dag = loads_qasm(
        _source(
            "h q[0]; c = measure q[0]; if (c) { x q[1]; } "
            "c = measure q[2]; if (!c) { z q[1]; } "
            "barrier q[0],q[1]; reset q[2];"
        )
    )
    measurements = [node for node in dag.nodes if node.name == "measure"]
    barrier = next(node for node in dag.nodes if node.name == "barrier")
    reset = next(node for node in dag.nodes if node.name == "reset")

    assert [node.result for node in measurements] == [ResultRef(1), ResultRef(3)]
    assert (measurements[0].id, measurements[1].id) in dag.edges
    assert (measurements[1].id, reset.id) in dag.edges
    assert barrier.qubits == (0, 1)
    assert reset.qubits == (2,)
    for node in dag.nodes:
        if node.source_id == 2:
            assert node.condition == Condition(ResultRef(1))
        elif node.source_id == 4:
            assert node.condition == Condition(ResultRef(3), 0)


def test_original_fixture_keeps_existing_measurement_identity() -> None:
    path = Path(__file__).parent / "fixtures" / "logical.qasm3"
    source_basis = GateSet(("h", "x", "p", "cx", "ccx", "swap", "rz"))
    source = load_qasm(path, gate_set=source_basis)
    native = load_qasm(path)

    assert [node.source_id for node in native.nodes if node.name == "measure"] == [
        13,
        21,
        30,
        40,
        51,
        63,
        76,
        86,
        99,
    ]
    assert [node.result for node in source.nodes if node.name == "measure"] == [
        node.result for node in native.nodes if node.name == "measure"
    ]
    assert {
        event.result: event.value
        for event in execute(source, FakeBackend(17)).measurements
    } == {
        event.result: event.value
        for event in execute(native, FakeBackend(17)).measurements
    }


def test_file_import_accepts_an_explicit_gate_set(tmp_path: Path) -> None:
    path = tmp_path / "native.qasm"
    source = _source("h q[0]; cx q[0],q[1];")
    path.write_text(source, encoding="utf-8")
    gate_set = GateSet(("u", "cz"))

    assert load_qasm(path, gate_set=gate_set) == loads_qasm(source, gate_set=gate_set)
    assert {node.name for node in load_qasm(path, gate_set=gate_set).nodes} <= {
        "u",
        "cz",
    }


def test_unrepresentable_gate_set_reports_source_context(tmp_path: Path) -> None:
    path = tmp_path / "incompatible.qasm"
    path.write_text(_source("h q[0];"), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_qasm(path, gate_set=GateSet(("rz", "cz")))

    assert str(path) in str(error.value)
    assert "h" in str(error.value)


@pytest.mark.parametrize("version", [2, 3])
def test_feedback_is_native_for_both_qasm_versions(version: int) -> None:
    source = (
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; creg c[1]; '
        "h q[0]; measure q[0] -> c[0]; if(c==1) cx q[0],q[1];"
        if version == 2
        else _source("h q[0]; c = measure q[0]; if (c) { cx q[0],q[1]; }")
    )
    dag = loads_qasm(source)
    children = [node for node in dag.nodes if node.source_id == 2]

    assert children
    assert {node.name for node in children} <= {
        *DEFAULT_GATE_SET.names,
        "global_phase",
    }
    assert all(node.condition == Condition(ResultRef(1)) for node in children)


def test_legacy_loader_import_is_the_same_native_frontend() -> None:
    from nadit_physical_compiler.qasm import loads_qasm as legacy_loads

    assert legacy_loads is loads_qasm


def test_repeated_gate_translation_is_cached_within_each_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = PassManager.run
    translated: list[str] = []

    def record(manager: PassManager, circuit: QuantumCircuit) -> QuantumCircuit:
        translated.append(circuit.data[0].operation.name)
        return run(manager, circuit)

    monkeypatch.setattr(PassManager, "run", record)
    source = _source("h q[0]; h q[1]; p(0.1) q[0]; p(0.1) q[2]; p(0.2) q[1];")

    loads_qasm(source)
    assert translated == ["h", "p", "p"]
    loads_qasm(source)
    assert translated == ["h", "p", "p", "h", "p", "p"]
