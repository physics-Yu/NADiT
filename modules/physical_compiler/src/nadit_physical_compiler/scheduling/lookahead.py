"""Preview the next resolved CZ layer without advancing compiler state."""

from collections.abc import Collection, Mapping, Sequence

from ..contracts import GateLayer, Scheduler
from ..physical_ir import PhysicalDAG, ResultRef


def next_cz_layer(
    dag: PhysicalDAG,
    current: GateLayer,
    *,
    ready: Collection[int],
    remaining: Sequence[int],
    successors: Sequence[Sequence[int]],
    results: Mapping[ResultRef, int],
    scheduler: Scheduler,
) -> GateLayer | None:
    """Preview the next CZ layer after completing the current CZ layer.

    Args:
        dag: Native operations and dependencies.
        current: CZ layer currently being compiled.
        ready: Compiler frontier before current completes.
        remaining: Current unfinished-predecessor counts, indexed by node ID.
        successors: Compiler adjacency lists, indexed by node ID.
        results: Measurement bits already available at the current planned time.
        scheduler: The compiler's deterministic, stateless layer selector.
            Preview also calls select and must not change its future decisions.

    Returns:
        The next selected CZ layer, or None at circuit end, unknown feedback,
        measurement, reset, or an unsupported operation. Empty current layers
        return None.

    Only the frontier is copied. A sparse decrement overlay tracks previewed
    completions; actual progress, measurement results, and phase stay unchanged.
    """
    if not current.operation_ids:
        return None
    frontier = set(ready)
    decrements: dict[int, int] = {}

    def finish(index: int) -> None:
        frontier.remove(index)
        for successor in successors[index]:
            decrements[successor] = decrements.get(successor, 0) + 1
            if decrements[successor] == remaining[successor]:
                frontier.add(successor)

    for index in current.operation_ids:
        finish(index)
    while frontier:
        ordered = tuple(sorted(frontier))
        if any(
            (condition := dag.nodes[index].condition) is not None
            and condition.result not in results
            for index in ordered
        ):
            return None
        changed = False
        for index in ordered:
            node = dag.nodes[index]
            if node.name in ("barrier", "global_phase") or (
                node.condition is not None
                and results[node.condition.result] != node.condition.value
            ):
                finish(index)
                changed = True
        if changed:
            continue
        selected = scheduler.select(dag, ordered)
        if not selected.operation_ids:
            return None
        names = tuple(dag.nodes[index].name for index in selected.operation_ids)
        if all(name == "cz" for name in names):
            return selected
        if not all(name in ("rx", "ry", "rz") for name in names):
            return None
        for index in selected.operation_ids:
            finish(index)
    return None
