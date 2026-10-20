"""CZ ready layers respect both qubit exclusivity and configured site capacity."""

import pytest

from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.scheduling import ReadyScheduler


def _cz_dag() -> PhysicalDAG:
    return PhysicalDAG(
        6,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "cz", (1, 2), (), ()),
            PhysicalOperation(2, "cz", (2, 3), (), ()),
            PhysicalOperation(3, "cz", (4, 5), (), ()),
        ),
        ((0, 1), (1, 2)),
    )


def test_cz_layer_selects_disjoint_pairs_up_to_capacity() -> None:
    dag = _cz_dag()
    ready = (3, 2, 1, 0)

    selected = ReadyScheduler(max_cz_gates=2).select(dag, ready)

    assert selected.operation_ids == (0, 2)
    assert ready == (3, 2, 1, 0)
    assert dag == _cz_dag()


def test_larger_capacity_accepts_all_disjoint_ready_pairs() -> None:
    assert ReadyScheduler(max_cz_gates=10).select(
        _cz_dag(), (3, 2, 1, 0)
    ).operation_ids == (0, 2, 3)


def test_default_capacity_keeps_one_cz_per_layer() -> None:
    assert ReadyScheduler().select(_cz_dag(), (0, 2, 3)).operation_ids == (0,)


@pytest.mark.parametrize("capacity", [0, -1])
def test_cz_capacity_must_be_positive(capacity: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        ReadyScheduler(max_cz_gates=capacity)


def test_earliest_ready_operation_still_selects_the_layer_type() -> None:
    dag = PhysicalDAG(
        6,
        0,
        (
            PhysicalOperation(0, "rz", (0,), (), (0.5,)),
            PhysicalOperation(1, "cz", (1, 2), (), ()),
            PhysicalOperation(2, "rz", (3,), (), (1.0,)),
            PhysicalOperation(3, "cz", (4, 5), (), ()),
        ),
        (),
    )
    scheduler = ReadyScheduler(max_cz_gates=2)

    assert scheduler.select(dag, (3, 2, 1, 0)).operation_ids == (0, 2)
    assert scheduler.select(dag, (1, 3)).operation_ids == (1, 3)


def test_cz_capacity_does_not_limit_addressed_operations_or_group_measurements() -> (
    None
):
    dag = PhysicalDAG(
        3,
        2,
        (
            PhysicalOperation(0, "reset", (0,), (), ()),
            PhysicalOperation(1, "reset", (1,), (), ()),
            PhysicalOperation(2, "reset", (2,), (), ()),
            PhysicalOperation(3, "measure", (0,), (0,), ()),
            PhysicalOperation(4, "measure", (1,), (1,), ()),
        ),
        ((0, 3), (1, 4)),
    )
    scheduler = ReadyScheduler(max_cz_gates=2)

    assert scheduler.select(dag, (0, 1, 2)).operation_ids == (0, 1, 2)
    assert scheduler.select(dag, (3, 4)).operation_ids == (3,)
