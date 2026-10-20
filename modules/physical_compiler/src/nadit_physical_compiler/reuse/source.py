"""Original endpoint-priority reuse and matching from pinned MQT QMAP.

Reference: MQT QMAP cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d (MIT).
"""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from collections import deque

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG


class SourceReuseAnalyzer:
    """Retain the source's first-endpoint graph and BFS/DFS matching decisions."""

    def analyze(
        self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
    ) -> tuple[int, ...]:
        """Return sorted reusable qubits using the unmodified source policy.

        Args:
            dag: Native circuit referenced by the layer node IDs.
            current: Current CZ layer, in scheduler order.
            following: Following resolved CZ layer, in scheduler order.

        Returns:
            Both endpoints of identical pairs and the endpoint selected by the
            original ``else-if`` graph for matched changed-partner gates.
        """
        before = _pairs(dag, current)
        after = _pairs(dag, following)
        old_gate = {qubit: index for index, pair in enumerate(before) for qubit in pair}
        retained: set[int] = set()
        adjacency: list[tuple[int, ...]] = []
        for first, second in after:
            left, right = old_gate.get(first), old_gate.get(second)
            if left is not None and right is not None and left == right:
                retained.update((first, second))
                adjacency.append(())
            elif left is not None:
                adjacency.append((left,))
            elif right is not None:
                adjacency.append((right,))
            else:
                adjacency.append(())
        for index, matched in enumerate(source_matching(tuple(adjacency))):
            if matched is not None:
                first, second = after[index]
                retained.add(first if old_gate.get(first) == matched else second)
        return tuple(sorted(retained))


def _pairs(dag: PhysicalDAG, layer: GateLayer) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(sorted(dag.nodes[index].qubits))
        for index in layer.operation_ids
        if dag.nodes[index].name == "cz"
    )


def source_matching(
    adjacency: tuple[tuple[int, ...], ...],
) -> tuple[int | None, ...]:
    """Execute the source's shortest-path BFS followed by stack-based DFS.

    Args:
        adjacency: Source-indexed ordered sink indices in the bipartite graph.

    Returns:
        Matched sink for every source, or None when that source stays unmatched.
    """
    free = [True] * len(adjacency)
    inverse: list[int | None] = [None] * (
        max((sink for row in adjacency for sink in row), default=0) + 1
    )
    while True:
        distance: list[int | None] = [0 if available else None for available in free]
        queue = deque(index for index, available in enumerate(free) if available)
        maximum: int | None = None
        while queue:
            source = queue.popleft()
            depth = distance[source]
            assert depth is not None
            if maximum is None or depth <= maximum:
                for sink in adjacency[source]:
                    matched = inverse[sink]
                    if matched is None:
                        maximum = depth
                    elif distance[matched] is None:
                        distance[matched] = depth + 1
                        queue.append(matched)
        if maximum is None:
            break
        for root, available in enumerate(free):
            if not available:
                continue
            stack = [root]
            parents: dict[int, tuple[int, int]] = {}
            found: tuple[int, int] | None = None
            while found is None and stack:
                source = stack.pop()
                depth = distance[source]
                assert depth is not None
                for sink in adjacency[source]:
                    matched = inverse[sink]
                    if matched is None:
                        found = source, sink
                        break
                    if distance[matched] == depth + 1:
                        parents[matched] = source, sink
                        stack.append(matched)
                distance[source] = None
            if found is not None:
                source, sink = found
                inverse[sink] = source
                while source != root:
                    source, sink = parents[source]
                    inverse[sink] = source
                free[root] = False
    matching: list[int | None] = [None] * len(adjacency)
    for sink, matched_source in enumerate(inverse):
        if matched_source is not None:
            matching[matched_source] = sink
    return tuple(matching)
