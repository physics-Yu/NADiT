"""Maximum-cardinality matching of shared qubits between CZ layers."""

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG


class MatchingReuseAnalyzer:
    """Retain endpoints using deterministic gate-to-gate matching.

    Each layer contains disjoint gates. An identical unordered pair is isolated
    from other CZ gates and retains both endpoints. Remaining matched gates
    retain their shared endpoint.
    """

    def analyze(
        self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
    ) -> tuple[int, ...]:
        """Return sorted physical-qubit IDs selected for reuse.

        Args:
            dag: Native operations referenced by the two layers.
            current: Selected current layer with conditions already resolved.
            following: Known following layer with disjoint CZ operations.

        Returns:
            Both endpoints of unchanged pairs and one shared endpoint for each
            matched changed pair. Non-CZ operations are ignored; a missing CZ
            layer produces no candidates.
        """
        before = _cz_pairs(dag, current)
        after = _cz_pairs(dag, following)
        after_by_pair = {pair: gate for gate, pair in after.items()}
        retained: set[int] = set()
        for gate, pair in tuple(before.items()):
            same_gate = after_by_pair.get(pair)
            if same_gate is not None:
                retained.update(pair)
                del before[gate]
                del after[same_gate]
        following_gate = {qubit: gate for gate, pair in after.items() for qubit in pair}
        adjacency = {
            gate: tuple(
                sorted(
                    {following_gate[qubit] for qubit in pair if qubit in following_gate}
                )
            )
            for gate, pair in before.items()
        }
        left_to_right: dict[int, int] = {}
        right_to_left: dict[int, int] = {}
        for gate in before:
            _augment(gate, adjacency, left_to_right, right_to_left)
        for old, new in left_to_right.items():
            retained.update(before[old] & after[new])
        return tuple(sorted(retained))


def _cz_pairs(dag: PhysicalDAG, layer: GateLayer) -> dict[int, frozenset[int]]:
    """Index only CZ gates in deterministic DAG-node order."""
    return {
        gate: frozenset(dag.nodes[gate].qubits)
        for gate in sorted(layer.operation_ids)
        if dag.nodes[gate].name == "cz"
    }


def _augment(
    start: int,
    adjacency: dict[int, tuple[int, ...]],
    left_to_right: dict[int, int],
    right_to_left: dict[int, int],
) -> None:
    """Extend the matching along the first available iterative DFS path."""
    stack = [(start, iter(adjacency[start]))]
    parents: dict[int, int] = {}
    while stack:
        left, neighbors = stack[-1]
        right = next(neighbors, None)
        if right is None:
            stack.pop()
            continue
        if right in parents:
            continue
        parents[right] = left
        matched = right_to_left.get(right)
        if matched is not None:
            stack.append((matched, iter(adjacency[matched])))
            continue
        while True:
            left = parents[right]
            previous = left_to_right.get(left)
            left_to_right[left] = right
            right_to_left[right] = left
            if previous is None:
                return
            right = previous
