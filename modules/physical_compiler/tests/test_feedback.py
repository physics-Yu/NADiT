"""Tests for measurement-result dependencies and conditional operations."""

from functools import partial
from math import pi
from pathlib import Path

import pytest

from nadit_physical_compiler import (
    Condition,
    FakeBackend,
    ResultRef,
    execute,
)
from nadit_physical_compiler import (
    load_qasm as _load_qasm,
)
from nadit_physical_compiler import (
    loads_qasm as _loads_qasm,
)
from nadit_physical_compiler.frontend import GateSet

# Keep the original feedback study's native basis and operation-count oracle.
TARGET_GATES = GateSet(("h", "x", "z", "rz", "p", "cx", "ccx"))
load_qasm = partial(_load_qasm, gate_set=TARGET_GATES)
loads_qasm = partial(_loads_qasm, gate_set=TARGET_GATES)


def _source(body: str, *, qubits: int = 3, clbits: int = 1) -> str:
    """Build an OpenQASM 3 circuit with standard gates."""
    return (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\n'
        f"qubit[{qubits}] q;\nbit[{clbits}] c;\n{body}"
    )


@pytest.mark.parametrize("value", [0, 1])
def test_qasm3_condition_reads_a_measurement_on_another_qubit(value: int) -> None:
    literal = "true" if value else "false"
    dag = loads_qasm(
        _source(f"c[0] = measure q[0]; if (c[0] == {literal}) {{ x q[1]; }}")
    )

    assert [node.name for node in dag.nodes] == ["measure", "x"]
    assert dag.nodes[0].condition is None
    assert dag.nodes[1].condition == Condition(ResultRef(0), value)
    assert dag.nodes[1].qubits == (1,)
    assert dag.edges == ((0, 1),)


def test_bare_bit_condition_reuses_the_same_result_for_multiple_operations() -> None:
    dag = loads_qasm(
        _source(
            "c[0] = measure q[0];\n"
            "if (c[0]) { x q[1]; }\n"
            "h q[2];\n"
            "if (c[0]) { z q[2]; }"
        )
    )

    assert dag.nodes[1].condition == Condition(ResultRef(0))
    assert dag.nodes[3].condition == Condition(ResultRef(0))
    assert dag.nodes[2].condition is None
    assert dag.edges == ((0, 1), (0, 3), (2, 3))


@pytest.mark.parametrize("value", [0, 1])
def test_qasm2_single_bit_register_condition(value: int) -> None:
    dag = loads_qasm(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
        "qreg q[2]; creg c[1];\n"
        f"measure q[0] -> c[0]; if(c=={value}) x q[1];"
    )

    assert [node.name for node in dag.nodes] == ["measure", "x"]
    assert dag.nodes[1].condition == Condition(ResultRef(0), value)
    assert dag.edges == ((0, 1),)


def test_if_else_blocks_keep_opposite_conditions_and_successor_dependencies() -> None:
    dag = loads_qasm(
        _source(
            "c[0] = measure q[0];\n"
            "if (c[0] == false) { x q[1]; reset q[2]; barrier q[1],q[2]; }\n"
            "else { z q[1]; }\n"
            "h q[1];"
        )
    )

    assert [node.name for node in dag.nodes] == [
        "measure",
        "x",
        "reset",
        "barrier",
        "z",
        "h",
    ]
    assert [node.condition for node in dag.nodes] == [
        None,
        Condition(ResultRef(0), 0),
        Condition(ResultRef(0), 0),
        Condition(ResultRef(0), 0),
        Condition(ResultRef(0), 1),
        None,
    ]
    assert {(0, 1), (0, 2), (0, 3), (0, 4), (1, 3), (2, 3), (4, 5)} <= set(dag.edges)


def test_remeasurement_does_not_rebind_existing_conditions() -> None:
    dag = loads_qasm(
        _source(
            "c[0] = measure q[0];\n"
            "if (c[0]) { x q[2]; }\n"
            "c[0] = measure q[1];\n"
            "if (c[0] == false) { z q[2]; }"
        )
    )

    assert [node.name for node in dag.nodes] == ["measure", "x", "measure", "z"]
    assert dag.nodes[1].condition == Condition(ResultRef(0), 1)
    assert dag.nodes[3].condition == Condition(ResultRef(2), 0)
    assert {(0, 1), (0, 2), (1, 3), (2, 3)} <= set(dag.edges)


def test_condition_propagates_through_parameterized_custom_gate_expansion() -> None:
    dag = loads_qasm(
        _source(
            "gate pair(theta) a,b { rz(theta) a; cx a,b; }\n"
            "c[0] = measure q[0];\n"
            "if (c[0]) { pair(pi/4) q[2],q[1]; }"
        )
    )

    assert [node.name for node in dag.nodes] == ["measure", "rz", "cx"]
    assert dag.nodes[1].params == pytest.approx((pi / 4,))
    assert dag.nodes[1].qubits == (2,)
    assert dag.nodes[2].qubits == (2, 1)
    assert dag.nodes[1].condition == dag.nodes[2].condition == Condition(ResultRef(0))
    assert dag.edges == ((0, 1), (0, 2), (1, 2))


def test_conditional_global_phase_keeps_its_guard_and_measurement_dependency() -> None:
    dag = loads_qasm(
        _source("gphase(pi/8); c[0] = measure q[0]; if (c[0]) { gphase(pi/4); }")
    )

    assert len(dag.nodes) == 2
    assert dag.nodes[1].name == "global_phase"
    assert dag.nodes[1].qubits == ()
    assert dag.nodes[1].params == pytest.approx((pi / 4,))
    assert dag.nodes[1].condition == Condition(ResultRef(0))
    assert dag.edges == ((0, 1),)
    assert dag.global_phase == pytest.approx(pi / 8)


def test_if_else_branches_map_local_wires_back_to_circuit_indices() -> None:
    dag = loads_qasm(
        _source(
            "c[0] = measure q[0]; if (c[0]) { cx q[3],q[1]; } else { x q[2]; }",
            qubits=4,
        )
    )

    assert [node.name for node in dag.nodes] == ["measure", "cx", "x"]
    assert [node.qubits for node in dag.nodes] == [(0,), (3, 1), (2,)]
    assert dag.nodes[1].condition == Condition(ResultRef(0), 1)
    assert dag.nodes[2].condition == Condition(ResultRef(0), 0)
    assert dag.edges == ((0, 1), (0, 2))


@pytest.mark.parametrize(
    "body",
    [
        "if (c[0]) { x q[0]; }",
        "c[0] = measure q[0]; if (c[0]) { c[1] = measure q[1]; }",
        "c[0] = measure q[0]; if (c[0]) { if (c[0]) { x q[1]; } }",
        "c[0] = measure q[0]; if (c == 1) { x q[1]; }",
        "c[0] = measure q[0]; c[1] = measure q[1]; if (c[0] && c[1]) { x q[2]; }",
        "c[0] = measure q[0]; while (c[0]) { x q[1]; }",
    ],
)
def test_unsupported_feedback_semantics_are_explicitly_rejected(body: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(_source(body, clbits=2))


def test_qasm2_multi_bit_register_condition_is_rejected() -> None:
    source = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
        "qreg q[2]; creg c[2];\n"
        "measure q[0] -> c[0]; if(c==1) x q[1];"
    )

    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(source)


def test_user_logical_circuit_preserves_all_feedback_dependencies() -> None:
    source_path = Path(__file__).parent / "fixtures" / "logical.qasm3"
    dag = load_qasm(source_path)
    measurements = [node for node in dag.nodes if node.name == "measure"]
    guarded = [node for node in dag.nodes if node.condition is not None]

    assert (dag.num_qubits, dag.num_clbits) == (14, 9)
    assert len(dag.nodes) == 117
    assert len(measurements) == 9
    assert len(guarded) == 36
    assert [node.clbits for node in measurements] == [(index,) for index in range(9)]
    assert dag == loads_qasm(source_path.read_text(encoding="utf-8"))

    guarded_index = 0
    for stage in range(1, 9):
        for result_index in range(stage):
            node = guarded[guarded_index]
            producer = measurements[result_index]
            assert node.name == "p"
            assert node.condition == Condition(ResultRef(producer.id), 1)
            assert node.qubits == (13 - stage,)
            assert node.params == pytest.approx((-pi / 2 ** (stage - result_index),))
            assert (producer.id, node.id) in dag.edges
            guarded_index += 1


def test_user_logical_circuit_executes_all_512_measurement_assignments() -> None:
    dag = load_qasm(Path(__file__).parent / "fixtures" / "logical.qasm3")
    measurements = [node for node in dag.nodes if node.name == "measure"]

    for assignment in range(512):
        bits = [(assignment >> index) & 1 for index in range(9)]
        overrides = {ResultRef(node.id): bits[node.clbits[0]] for node in measurements}
        trace = execute(
            dag,
            FakeBackend(overrides=overrides),
            durations={"measure": 2.0, "p": 0.25, "h": 0.5},
            measurement_latency=3.0,
        )
        events = {event.operation_id: event for event in trace.operations}
        results = {event.result: event for event in trace.measurements}

        assert len(trace.measurements) == 9
        assert {result: event.value for result, event in results.items()} == overrides
        assert len(trace.operations) == 117
        assert set(events) == set(range(117))
        assert sum(event.status == "executed" for event in trace.operations) == (
            81 + sum((8 - index) * bits[index] for index in range(8))
        )
        for node in dag.nodes:
            event = events[node.id]
            if node.condition is None:
                assert event.status == "executed"
            else:
                assert node.name == "p"
                result = results[node.condition.result]
                expected = (
                    "executed" if result.value == node.condition.value else "skipped"
                )
                assert event.status == expected
                assert event.start >= result.available_at
        for source, target in dag.edges:
            assert events[target].start >= events[source].end


def test_user_logical_circuit_seed_is_repeatable_across_measurement_latencies() -> None:
    dag = load_qasm(Path(__file__).parent / "fixtures" / "logical.qasm3")
    first = execute(dag, FakeBackend(seed=17), measurement_latency=2.0)
    repeated = execute(dag, FakeBackend(seed=17), measurement_latency=2.0)
    delayed = execute(dag, FakeBackend(seed=17), measurement_latency=10.0)

    assert first == repeated
    assert {event.result: event.value for event in first.measurements} == {
        event.result: event.value for event in delayed.measurements
    }
    assert [
        event.operation_id for event in first.operations if event.status == "executed"
    ] == [
        event.operation_id for event in delayed.operations if event.status == "executed"
    ]
    assert delayed.duration > first.duration
