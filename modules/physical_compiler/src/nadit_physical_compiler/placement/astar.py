"""Source A* tree search using the shared placement score."""

# Adapted from MQT QMAP HeuristicPlacer.hpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from collections.abc import Callable, Iterable
from heapq import heappop, heappush
from itertools import count


def a_star_tree_search[Node](
    initial: Node,
    *,
    expand: Callable[[Node], Iterable[Node]],
    is_goal: Callable[[Node], bool],
    cost: Callable[[Node], float],
    heuristic: Callable[[Node], float],
    max_nodes: int = 10_000_000,
) -> Node:
    """Return the first goal popped from the source best-first search.

    Args:
        initial: Search root with zero accumulated cost.
        expand: Generate the children of a non-goal node.
        is_goal: Identify a completed assignment.
        cost: Total accumulated node cost.
        heuristic: Estimated remaining cost.
        max_nodes: Abort when the open set reaches this size after expansion.

    Returns:
        The first completed node in cost-plus-heuristic order. Equal priorities
        preserve generation order; the upstream C++ heap does not specify ties.

    Raises:
        ValueError: If max_nodes is nonpositive, the open-set limit is reached,
            or no complete assignment exists.

    This is tree search, with no closed set. Placement heuristics need not be
    admissible, so the returned assignment is not an optimality certificate.
    """
    if max_nodes <= 0:
        raise ValueError("A* max_nodes must be greater than zero.")
    serials = count()
    queue = [(heuristic(initial), next(serials), initial)]
    while queue:
        _, _, node = heappop(queue)
        if is_goal(node):
            return node
        for child in expand(node):
            heappush(queue, (cost(child) + heuristic(child), next(serials), child))
        if len(queue) >= max_nodes:
            raise ValueError("A* reached max_nodes in its open set.")
    raise ValueError("A* found no completed goal in the candidate tree.")
