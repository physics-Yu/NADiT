"""Bounded iterative diving search with deterministic priority ties."""

# Adapted from MQT QMAP HeuristicPlacer.hpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from collections.abc import Callable, Iterable
from heapq import heapify, heappop, heappush


class _BoundedQueue[Node]:
    """Keep the best alternatives with paired heaps and bounded stale storage."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._serial = 0
        self._items: dict[int, tuple[float, Node]] = {}
        self._best: list[tuple[float, int]] = []
        self._worst: list[tuple[float, int]] = []

    def __bool__(self) -> bool:
        return bool(self._items)

    def push(self, priority: float, node: Node) -> None:
        if self.capacity == 0:
            return
        serial = self._serial
        self._serial += 1
        if len(self._items) == self.capacity:
            while -self._worst[0][1] not in self._items:
                heappop(self._worst)
            worst_priority, worst_serial = self._worst[0]
            if (priority, serial) >= (-worst_priority, -worst_serial):
                return
            heappop(self._worst)
            del self._items[-worst_serial]
        self._items[serial] = (priority, node)
        heappush(self._best, (priority, serial))
        heappush(self._worst, (-priority, -serial))
        self._compact()

    def pop(self, *, shrink: bool = False) -> tuple[float, Node]:
        while self._best[0][1] not in self._items:
            heappop(self._best)
        _, serial = heappop(self._best)
        item = self._items.pop(serial)
        if shrink:
            self.capacity -= 1
        self._compact()
        return item

    def _compact(self) -> None:
        limit = 2 * max(1, self.capacity)
        if len(self._best) > limit or len(self._worst) > limit:
            self._best = [
                (priority, serial) for serial, (priority, _) in self._items.items()
            ]
            self._worst = [(-priority, -serial) for priority, serial in self._best]
            heapify(self._best)
            heapify(self._worst)


def iterative_diving_search[Node](
    initial: Node,
    *,
    expand: Callable[[Node], Iterable[Node]],
    is_goal: Callable[[Node], bool],
    cost: Callable[[Node], float],
    heuristic: Callable[[Node], float],
    trials: int = 4,
    queue_capacity: int = 100,
) -> Node:
    """Dive into best children and restart from bounded queued alternatives.

    Args:
        initial: Initial search-tree node, with zero accumulated cost.
        expand: Generate children once for each visited non-goal node.
        is_goal: Identify completed nodes.
        cost: Accumulated node score.
        heuristic: Estimated remaining score; placement goals have zero.
        trials: Maximum number of completed goals to compare.
        queue_capacity: Base alternative capacity; initially add the trial count.

    Returns:
        The completed node with lowest cost plus heuristic among visited goals.
        Equal priorities preserve generation order.

    Raises:
        ValueError: If budgets are invalid or the bounded search finds no goal.

    Goal restarts pop an alternative and shrink capacity by one. Dead ends pop
    without shrinking and do not consume trials.
    """
    if trials < 1 or queue_capacity < 0:
        raise ValueError("IDS requires positive trials and nonnegative queue capacity.")
    queue = _BoundedQueue[Node](queue_capacity + trials)
    current = (heuristic(initial), initial)
    goal: tuple[float, Node] | None = None
    while True:
        priority, node = current
        if is_goal(node):
            trials -= 1
            if goal is None or priority < goal[0]:
                goal = current
            if trials and queue:
                current = queue.pop(shrink=True)
                continue
            break
        best: tuple[float, Node] | None = None
        for child in expand(node):
            child_priority = cost(child) + heuristic(child)
            if best is None:
                best = (child_priority, child)
            elif child_priority < best[0]:
                queue.push(*best)
                best = (child_priority, child)
            else:
                queue.push(child_priority, child)
        if best is not None:
            current = best
        elif queue:
            current = queue.pop()
        else:
            break
    if goal is None:
        raise ValueError("IDS found no completed goal within its bounded search.")
    return goal[1]
