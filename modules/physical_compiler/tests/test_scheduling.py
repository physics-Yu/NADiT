"""Ready-layer selection without a second dependency-progress owner."""

import pytest

from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.scheduling import ReadyScheduler


def test_empty_ready_set_returns_empty_layer() -> None:
    assert ReadyScheduler().select(PhysicalDAG(0, 0, (), ()), ()).operation_ids == ()


def test_layer_groups_disjoint_operations_of_earliest_ready_name() -> None:
    dag = PhysicalDAG(
        3,
        0,
        (
            PhysicalOperation(0, "rx", (0,), (), (0.5,)),
            PhysicalOperation(1, "ry", (1,), (), (0.5,)),
            PhysicalOperation(2, "rx", (2,), (), (1.0,)),
        ),
        (),
    )

    assert ReadyScheduler().select(dag, (2, 1, 0)).operation_ids == (0, 2)


def test_layer_does_not_repeat_a_qubit() -> None:
    dag = PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, "reset", (0,), (), ()),
            PhysicalOperation(1, "reset", (0,), (), ()),
            PhysicalOperation(2, "reset", (1,), (), ()),
        ),
        ((0, 1),),
    )

    assert ReadyScheduler().select(dag, (0, 1, 2)).operation_ids == (0, 2)


@pytest.mark.parametrize("name", ["cz", "measure"])
def test_two_qubit_gates_and_readout_use_one_operation_per_layer(name: str) -> None:
    qubits = ((0, 1), (2, 3)) if name == "cz" else ((0,), (1,))
    clbits = ((), ()) if name == "cz" else ((0,), (1,))
    dag = PhysicalDAG(
        4,
        2,
        tuple(
            PhysicalOperation(index, name, qs, cs, ())
            for index, (qs, cs) in enumerate(zip(qubits, clbits, strict=True))
        ),
        (),
    )

    assert ReadyScheduler().select(dag, (1, 0)).operation_ids == (0,)
