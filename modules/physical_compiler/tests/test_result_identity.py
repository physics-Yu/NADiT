"""Source measurement identities survive native gate expansion."""

import pytest

from nadit_physical_compiler.execution import (
    FakeBackend,
    MeasurementEvent,
    OperationEvent,
    execute,
)
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)


def test_operation_defaults_source_identity_to_its_local_id() -> None:
    operation = PhysicalOperation(7, "measure", (0,), (0,), ())

    assert operation.source_id == 7
    assert operation.result == ResultRef(7)
    assert operation == PhysicalOperation(7, "measure", (0,), (0,), (), source_id=7)


def test_only_measurements_produce_result_references() -> None:
    assert PhysicalOperation(0, "rz", (0,), (), (0.5,), source_id=7).result is None
    assert PhysicalOperation(
        0, "measure", (0,), (0,), (), source_id=7
    ).result == ResultRef(7)


def test_condition_references_source_measurement_not_native_node_index() -> None:
    dag = PhysicalDAG(
        num_qubits=2,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), (), source_id=100),
            PhysicalOperation(
                1, "rx", (1,), (), (0.5,), Condition(ResultRef(100)), source_id=101
            ),
        ),
        edges=((0, 1),),
    )

    assert dag.nodes[1].condition is not None
    assert dag.nodes[0].result == dag.nodes[1].condition.result
    assert dag.edges == ((0, 1),)


def test_native_children_can_share_a_nonmeasurement_source_identity() -> None:
    dag = PhysicalDAG(
        num_qubits=1,
        num_clbits=0,
        nodes=(
            PhysicalOperation(0, "rz", (0,), (), (0.5,), source_id=8),
            PhysicalOperation(1, "rx", (0,), (), (1.0,), source_id=8),
        ),
        edges=((0, 1),),
    )

    assert tuple(node.source_id for node in dag.nodes) == (8, 8)


def test_duplicate_measurement_source_identities_are_rejected() -> None:
    with pytest.raises(ValueError, match="measurement|Measurement"):
        PhysicalDAG(
            num_qubits=1,
            num_clbits=1,
            nodes=(
                PhysicalOperation(0, "measure", (0,), (0,), (), source_id=100),
                PhysicalOperation(1, "measure", (0,), (0,), (), source_id=100),
            ),
            edges=((0, 1),),
        )


@pytest.mark.parametrize("result", [ResultRef(0), ResultRef(101)])
def test_condition_cannot_reference_local_id_or_future_measurement(
    result: ResultRef,
) -> None:
    with pytest.raises(ValueError, match="earlier measurement"):
        PhysicalDAG(
            num_qubits=2,
            num_clbits=1,
            nodes=(
                PhysicalOperation(0, "measure", (0,), (0,), (), source_id=100),
                PhysicalOperation(
                    1, "rx", (1,), (), (0.5,), Condition(result), source_id=102
                ),
                PhysicalOperation(2, "measure", (0,), (0,), (), source_id=101),
            ),
            edges=((0, 1), (0, 2)),
        )


def _expanded_feedback_dag(prefix_count: int) -> PhysicalDAG:
    prefix = tuple(
        PhysicalOperation(index, "rz", (0,), (), (0.25,), source_id=0)
        for index in range(prefix_count)
    )
    return PhysicalDAG(
        num_qubits=2,
        num_clbits=1,
        nodes=prefix
        + (
            PhysicalOperation(prefix_count, "measure", (0,), (0,), (), source_id=100),
            PhysicalOperation(
                prefix_count + 1,
                "rx",
                (1,),
                (),
                (0.5,),
                Condition(ResultRef(100)),
                source_id=101,
            ),
            PhysicalOperation(prefix_count + 2, "rz", (1,), (), (0.5,), source_id=102),
        ),
        edges=tuple((index, index + 1) for index in range(prefix_count + 2)),
    )


def test_execution_queries_the_same_result_after_native_node_offsets_change() -> None:
    class RecordingBackend:
        def __init__(self) -> None:
            self.calls: list[ResultRef] = []

        def measure(self, result: ResultRef) -> int:
            self.calls.append(result)
            return FakeBackend(seed=17).measure(result)

    first_backend = RecordingBackend()
    second_backend = RecordingBackend()
    first = execute(_expanded_feedback_dag(1), first_backend)
    second = execute(_expanded_feedback_dag(4), second_backend)

    assert first_backend.calls == second_backend.calls == [ResultRef(100)]
    assert first.measurements[0].value == second.measurements[0].value
    assert first.operations[-2].status == second.operations[-2].status


def test_source_result_condition_skips_after_latency_and_releases_successor() -> None:
    trace = execute(
        _expanded_feedback_dag(2),
        FakeBackend(overrides={ResultRef(100): 0}),
        durations={"rz": 0.5, "measure": 2.0},
        measurement_latency=3.0,
    )

    assert trace.measurements == (MeasurementEvent(ResultRef(100), 0, 6.0),)
    assert trace.operations[-2:] == (
        OperationEvent(3, "skipped", 6.0, 6.0),
        OperationEvent(4, "executed", 6.0, 6.5),
    )


def test_repeated_classical_write_keeps_distinct_source_results() -> None:
    dag = PhysicalDAG(
        num_qubits=2,
        num_clbits=1,
        nodes=(
            PhysicalOperation(0, "measure", (0,), (0,), (), source_id=100),
            PhysicalOperation(1, "measure", (0,), (0,), (), source_id=200),
            PhysicalOperation(
                2, "rx", (1,), (), (0.5,), Condition(ResultRef(100)), source_id=201
            ),
            PhysicalOperation(
                3, "rz", (1,), (), (0.5,), Condition(ResultRef(200)), source_id=202
            ),
        ),
        edges=((0, 1), (0, 2), (1, 3), (2, 3)),
    )

    trace = execute(dag, FakeBackend(overrides={ResultRef(100): 1, ResultRef(200): 0}))

    assert tuple(event.result for event in trace.measurements) == (
        ResultRef(100),
        ResultRef(200),
    )
    assert tuple(event.status for event in trace.operations[-2:]) == (
        "executed",
        "skipped",
    )
