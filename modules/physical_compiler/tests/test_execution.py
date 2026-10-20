"""Deterministic feedback execution tests without a quantum-state simulator."""

from math import inf, nan

import pytest

from nadit_physical_compiler.execution import (
    ExecutionTrace,
    FakeBackend,
    MeasurementEvent,
    OperationEvent,
    execute,
)
from nadit_physical_compiler.ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)


def feedback_dag() -> PhysicalDAG:
    """Build a measurement, an independent gate, and a guarded successor."""
    return PhysicalDAG(
        num_qubits=3,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), ()),
            PhysicalOperation(1, "h", (1,), (), ()),
            PhysicalOperation(2, "x", (2,), (), (), Condition(ResultRef(0))),
            PhysicalOperation(3, "z", (2,), (), ()),
            PhysicalOperation(4, "reset", (0,), (), ()),
        ),
        edges=((0, 2), (0, 4), (2, 3)),
    )


def test_feedback_waits_for_result_without_blocking_independent_work() -> None:
    trace = execute(
        feedback_dag(),
        FakeBackend(overrides={ResultRef(0): 1}),
        durations={"measure": 2.0, "h": 0.5, "x": 3.0},
        measurement_latency=5.0,
    )

    assert trace.operations == (
        OperationEvent(0, "executed", 0.0, 2.0),
        OperationEvent(1, "executed", 0.0, 0.5),
        OperationEvent(2, "executed", 7.0, 10.0),
        OperationEvent(3, "executed", 10.0, 11.0),
        OperationEvent(4, "executed", 2.0, 3.0),
    )
    assert trace.measurements == (MeasurementEvent(ResultRef(0), 1, 7.0),)
    assert trace.duration == 11.0


def test_false_condition_skips_operation_and_releases_successors() -> None:
    trace = execute(
        feedback_dag(),
        FakeBackend(overrides={ResultRef(0): 0}),
        measurement_latency=3.0,
    )

    assert trace.operations[2] == OperationEvent(2, "skipped", 4.0, 4.0)
    assert trace.operations[3] == OperationEvent(3, "executed", 4.0, 5.0)
    assert trace.duration == 5.0


def test_condition_waits_for_both_result_and_quantum_predecessor() -> None:
    dag = PhysicalDAG(
        num_qubits=2,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), ()),
            PhysicalOperation(1, "slow", (1,), (), ()),
            PhysicalOperation(2, "x", (1,), (), (), Condition(ResultRef(0), 0)),
        ),
        edges=((0, 2), (1, 2)),
    )

    trace = execute(
        dag,
        FakeBackend(overrides={ResultRef(0): 0}),
        durations={"slow": 9.0},
        measurement_latency=2.0,
    )

    assert trace.measurements[0].available_at == 3.0
    assert trace.operations[2] == OperationEvent(2, "executed", 9.0, 10.0)


def test_reused_classical_wire_preserves_each_measurement_result() -> None:
    dag = PhysicalDAG(
        num_qubits=3,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), ()),
            PhysicalOperation(1, "x", (1,), (), (), Condition(ResultRef(0))),
            PhysicalOperation(2, "measure", (0,), (0,), ()),
            PhysicalOperation(3, "z", (2,), (), (), Condition(ResultRef(2))),
        ),
        edges=((0, 1), (0, 2), (1, 2), (2, 3)),
    )
    trace = execute(dag, FakeBackend(overrides={ResultRef(0): 1, ResultRef(2): 0}))

    assert trace.operations[1].status == "executed"
    assert trace.operations[3].status == "skipped"
    assert tuple(event.value for event in trace.measurements) == (1, 0)


def test_backend_is_queried_when_results_arrive_not_in_source_order() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.calls: list[ResultRef] = []

        def measure(self, result: ResultRef) -> int:
            self.calls.append(result)
            return 1

    dag = PhysicalDAG(
        num_qubits=2,
        num_clbits=2,
        nodes=(
            PhysicalOperation(0, "slow", (0,), (), ()),
            PhysicalOperation(1, "measure", (0,), (0,), ()),
            PhysicalOperation(2, "measure", (1,), (1,), ()),
        ),
        edges=((0, 1),),
    )
    backend = RecordingBackend()

    trace = execute(dag, backend, durations={"slow": 5.0}, measurement_latency=2.0)

    assert backend.calls == [ResultRef(2), ResultRef(1)]
    assert trace.measurements == (
        MeasurementEvent(ResultRef(2), 1, 3.0),
        MeasurementEvent(ResultRef(1), 1, 8.0),
    )


def test_several_conditions_reuse_one_delivered_measurement_bit() -> None:
    class CountingBackend:
        def __init__(self) -> None:
            self.calls = 0

        def measure(self, result: ResultRef) -> int:
            self.calls += 1
            return self.calls % 2

    dag = PhysicalDAG(
        num_qubits=4,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), ()),
            PhysicalOperation(1, "x", (1,), (), (), Condition(ResultRef(0))),
            PhysicalOperation(2, "y", (2,), (), (), Condition(ResultRef(0))),
            PhysicalOperation(3, "z", (3,), (), (), Condition(ResultRef(0), 0)),
        ),
        edges=((0, 1), (0, 2), (0, 3)),
    )
    backend = CountingBackend()

    trace = execute(dag, backend, measurement_latency=2.0)

    assert backend.calls == 1
    assert trace.operations[1:] == (
        OperationEvent(1, "executed", 3.0, 4.0),
        OperationEvent(2, "executed", 3.0, 4.0),
        OperationEvent(3, "skipped", 3.0, 3.0),
    )


def test_fake_results_are_independent_of_query_order() -> None:
    results = tuple(ResultRef(index) for index in range(32))
    forward = FakeBackend(seed=17)
    backward = FakeBackend(seed=17)

    assert {result: forward.measure(result) for result in results} == {
        result: backward.measure(result) for result in reversed(results)
    }


def test_fake_results_depend_on_seed_and_accept_explicit_overrides() -> None:
    results = tuple(ResultRef(index) for index in range(32))
    first = FakeBackend(seed=17)
    second = FakeBackend(seed=23)
    overridden = FakeBackend(seed=17, overrides={ResultRef(0): 0, ResultRef(1): 1})

    assert any(first.measure(result) != second.measure(result) for result in results)
    assert overridden.measure(ResultRef(0)) == 0
    assert overridden.measure(ResultRef(1)) == 1
    assert overridden.measure(ResultRef(2)) == first.measure(ResultRef(2))


def test_execution_can_repeat_with_same_backend_and_has_no_retained_state() -> None:
    dag = feedback_dag()
    backend = FakeBackend(seed=19)

    first = execute(dag, backend, measurement_latency=2.0)
    second = execute(dag, backend, measurement_latency=2.0)

    assert first == second
    assert len(first.operations) == len(dag.nodes)
    assert len(first.measurements) == 1


def test_default_backend_is_reproducible() -> None:
    assert execute(feedback_dag()) == execute(feedback_dag(), FakeBackend(seed=0))


def test_empty_dag_has_empty_zero_duration_trace() -> None:
    trace = execute(PhysicalDAG(num_qubits=0, num_clbits=0, nodes=(), edges=()))

    assert trace == ExecutionTrace(operations=(), measurements=())
    assert trace.duration == 0.0


def test_trace_duration_includes_measurement_result_latency() -> None:
    dag = PhysicalDAG(
        num_qubits=1,
        num_clbits=1,
        nodes=(PhysicalOperation(0, "measure", (0,), (0,), ()),),
        edges=(),
    )

    trace = execute(dag, measurement_latency=4.0)

    assert trace.operations[0].end == 1.0
    assert trace.duration == 5.0


def test_barrier_and_global_phase_take_no_default_time() -> None:
    dag = PhysicalDAG(
        num_qubits=1,
        num_clbits=0,
        nodes=(
            PhysicalOperation(0, "h", (0,), (), ()),
            PhysicalOperation(1, "barrier", (0,), (), ()),
            PhysicalOperation(2, "global_phase", (), (), (0.25,)),
            PhysicalOperation(3, "x", (0,), (), ()),
        ),
        edges=((0, 1), (1, 3)),
    )

    trace = execute(dag)

    assert trace.operations[1] == OperationEvent(1, "executed", 1.0, 1.0)
    assert trace.operations[2] == OperationEvent(2, "executed", 0.0, 0.0)
    assert trace.operations[3] == OperationEvent(3, "executed", 1.0, 2.0)


@pytest.mark.parametrize("value", [2, -1])
def test_non_bit_backend_values_are_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="measurement|Measurement"):
        execute(feedback_dag(), FakeBackend(overrides={ResultRef(0): value}))


@pytest.mark.parametrize("duration", [-1.0, inf, nan])
def test_operation_duration_must_be_nonnegative_and_finite(duration: float) -> None:
    with pytest.raises(ValueError, match="duration|Duration"):
        execute(feedback_dag(), durations={"measure": duration})


@pytest.mark.parametrize("latency", [-1.0, inf, nan])
def test_measurement_latency_must_be_nonnegative_and_finite(latency: float) -> None:
    with pytest.raises(ValueError, match="latency|Latency"):
        execute(feedback_dag(), measurement_latency=latency)
