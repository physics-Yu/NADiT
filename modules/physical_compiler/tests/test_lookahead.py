"""Pure next-CZ preview across known local operations and classical branches."""

import pytest

from nadit_physical_compiler.contracts import GateLayer, Scheduler
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.scheduling import ReadyScheduler, next_cz_layer


def _progress(
    dag: PhysicalDAG, completed: tuple[int, ...] = ()
) -> tuple[set[int], list[int], list[list[int]]]:
    successors: list[list[int]] = [[] for _ in dag.nodes]
    remaining = [0] * len(dag.nodes)
    for before, after in dag.edges:
        successors[before].append(after)
        remaining[after] += 1
    ready = {index for index, count in enumerate(remaining) if count == 0}
    for index in completed:
        ready.remove(index)
        for successor in successors[index]:
            remaining[successor] -= 1
            if remaining[successor] == 0:
                ready.add(successor)
    return ready, remaining, successors


def test_preview_crosses_local_and_metadata_layers_without_mutating_progress() -> None:
    dag = PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "rx", (0,), (), (0.5,)),
            PhysicalOperation(2, "rz", (1,), (), (-0.25,)),
            PhysicalOperation(3, "barrier", (0, 1), (), ()),
            PhysicalOperation(4, "global_phase", (), (), (0.75,)),
            PhysicalOperation(5, "cz", (0, 1), (), ()),
        ),
        ((0, 1), (0, 2), (1, 3), (2, 3), (3, 4), (4, 5)),
        global_phase=0.25,
    )
    ready, remaining, successors = _progress(dag)
    before = (set(ready), list(remaining), [list(row) for row in successors])
    results: dict[ResultRef, int] = {}

    following = next_cz_layer(
        dag,
        GateLayer((0,)),
        ready=ready,
        remaining=remaining,
        successors=successors,
        results=results,
        scheduler=ReadyScheduler(),
    )

    assert following == GateLayer((5,))
    assert (ready, remaining, successors) == before
    assert results == {}
    assert dag.global_phase == 0.25


@pytest.mark.parametrize("capacity", [1, 2])
def test_preview_preserves_scheduler_capacity_splitting(capacity: int) -> None:
    dag = PhysicalDAG(
        6,
        0,
        tuple(
            PhysicalOperation(index, "cz", (2 * index, 2 * index + 1), (), ())
            for index in range(3)
        ),
        (),
    )
    ready, remaining, successors = _progress(dag)
    scheduler = ReadyScheduler(max_cz_gates=capacity)
    current = scheduler.select(dag, tuple(ready))

    following = next_cz_layer(
        dag,
        current,
        ready=ready,
        remaining=remaining,
        successors=successors,
        results={},
        scheduler=scheduler,
    )

    assert following == GateLayer((1,) if capacity == 1 else (2,))


@pytest.mark.parametrize("bit,expected", [(1, 2), (0, 3)])
def test_known_conditions_skip_false_branch_and_choose_enabled_cz(
    bit: int, expected: int
) -> None:
    reference = ResultRef(90)
    dag = PhysicalDAG(
        2,
        1,
        (
            PhysicalOperation(0, "measure", (0,), (0,), (), source_id=90),
            PhysicalOperation(1, "cz", (0, 1), (), ()),
            PhysicalOperation(2, "cz", (0, 1), (), (), Condition(reference)),
            PhysicalOperation(3, "cz", (0, 1), (), (), Condition(reference, 0)),
        ),
        ((0, 1), (0, 2), (0, 3), (1, 2), (2, 3)),
    )
    ready, remaining, successors = _progress(dag, (0,))
    results = {reference: bit}

    following = next_cz_layer(
        dag,
        GateLayer((1,)),
        ready=ready,
        remaining=remaining,
        successors=successors,
        results=results,
        scheduler=ReadyScheduler(),
    )

    assert following == GateLayer((expected,))
    assert results == {reference: bit}


@pytest.mark.parametrize("bit", [0, 1])
def test_conditional_metadata_and_local_children_are_resolved_repeatedly(
    bit: int,
) -> None:
    reference = ResultRef(91)
    dag = PhysicalDAG(
        2,
        1,
        (
            PhysicalOperation(0, "measure", (0,), (0,), (), source_id=91),
            PhysicalOperation(1, "cz", (0, 1), (), ()),
            PhysicalOperation(2, "global_phase", (), (), (0.75,), Condition(reference)),
            PhysicalOperation(3, "ry", (1,), (), (0.5,), Condition(reference)),
            PhysicalOperation(4, "cz", (0, 1), (), ()),
        ),
        ((0, 1), (0, 2), (0, 3), (1, 2), (2, 3), (3, 4)),
        global_phase=0.25,
    )
    ready, remaining, successors = _progress(dag, (0,))

    following = next_cz_layer(
        dag,
        GateLayer((1,)),
        ready=ready,
        remaining=remaining,
        successors=successors,
        results={reference: bit},
        scheduler=ReadyScheduler(),
    )

    assert following == GateLayer((4,))
    assert dag.global_phase == 0.25


def test_measurement_stops_preview_before_its_future_condition() -> None:
    dag = PhysicalDAG(
        2,
        1,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "measure", (0,), (0,), (), source_id=100),
            PhysicalOperation(2, "cz", (0, 1), (), (), Condition(ResultRef(100))),
        ),
        ((0, 1), (1, 2)),
    )
    ready, remaining, successors = _progress(dag)

    assert (
        next_cz_layer(
            dag,
            GateLayer((0,)),
            ready=ready,
            remaining=remaining,
            successors=successors,
            results={},
            scheduler=ReadyScheduler(),
        )
        is None
    )


def test_unknown_frontier_result_stops_even_when_an_independent_cz_is_ready() -> None:
    dag = PhysicalDAG(
        3,
        1,
        (
            PhysicalOperation(0, "measure", (2,), (0,), (), source_id=100),
            PhysicalOperation(1, "cz", (0, 1), (), ()),
            PhysicalOperation(2, "cz", (0, 1), (), ()),
            PhysicalOperation(3, "rx", (2,), (), (0.5,), Condition(ResultRef(100))),
        ),
        ((0, 3), (1, 2)),
    )
    ready, remaining, successors = _progress(dag, (0,))

    assert (
        next_cz_layer(
            dag,
            GateLayer((1,)),
            ready=ready,
            remaining=remaining,
            successors=successors,
            results={},
            scheduler=ReadyScheduler(),
        )
        is None
    )


@pytest.mark.parametrize("name", ["reset", "h", "delay"])
def test_nonlocal_or_unsupported_selected_operation_stops_preview(name: str) -> None:
    dag = PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, name, (0,), (), ()),
            PhysicalOperation(2, "cz", (0, 1), (), ()),
        ),
        ((0, 1), (1, 2)),
    )
    ready, remaining, successors = _progress(dag)

    assert (
        next_cz_layer(
            dag,
            GateLayer((0,)),
            ready=ready,
            remaining=remaining,
            successors=successors,
            results={},
            scheduler=ReadyScheduler(),
        )
        is None
    )


def test_preview_uses_the_supplied_deterministic_scheduler() -> None:
    class LatestFirst:
        def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
            return GateLayer((max(ready),))

    dag = PhysicalDAG(
        3,
        1,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "measure", (2,), (0,), ()),
            PhysicalOperation(2, "cz", (0, 1), (), ()),
        ),
        ((0, 1), (0, 2)),
    )
    ready, remaining, successors = _progress(dag)
    scheduler: Scheduler = LatestFirst()

    assert next_cz_layer(
        dag,
        GateLayer((0,)),
        ready=ready,
        remaining=remaining,
        successors=successors,
        results={},
        scheduler=scheduler,
    ) == GateLayer((2,))


@pytest.mark.parametrize("current", [GateLayer(()), GateLayer((0,))])
def test_empty_or_terminal_current_layer_has_no_following_cz(
    current: GateLayer,
) -> None:
    dag = PhysicalDAG(2, 0, (PhysicalOperation(0, "cz", (0, 1), (), ()),), ())
    ready, remaining, successors = _progress(dag)

    assert (
        next_cz_layer(
            dag,
            current,
            ready=ready,
            remaining=remaining,
            successors=successors,
            results={},
            scheduler=ReadyScheduler(),
        )
        is None
    )
