"""Indexed paired heaps preserving the source IDS queue's tie decisions."""

# Adapted from MQT QMAP HeuristicPlacer.hpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from dataclasses import dataclass


@dataclass(slots=True)
class _Entry[Node]:
    priority: float
    node: Node
    minimum_index: int
    maximum_index: int


class _BoundedQueue[Node]:
    """Retain the best alternatives with the original paired heap operations.

    Equal priorities are compared without an insertion serial. Their removal
    order follows the source's swaps, strict comparisons, and left-child-first
    heap restoration. Each entry occupies one slot in each heap.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._minimum: list[_Entry[Node]] = []
        self._maximum: list[_Entry[Node]] = []

    def __bool__(self) -> bool:
        return bool(self._minimum)

    def _swap_minimum(self, first: int, second: int) -> None:
        heap = self._minimum
        heap[first], heap[second] = heap[second], heap[first]
        heap[first].minimum_index = first
        heap[second].minimum_index = second

    def _swap_maximum(self, first: int, second: int) -> None:
        heap = self._maximum
        heap[first], heap[second] = heap[second], heap[first]
        heap[first].maximum_index = first
        heap[second].maximum_index = second

    def _minimum_up(self, index: int) -> None:
        heap = self._minimum
        while index:
            parent = (index - 1) // 2
            if not heap[index].priority < heap[parent].priority:
                break
            self._swap_minimum(index, parent)
            index = parent

    def _maximum_up(self, index: int) -> None:
        heap = self._maximum
        while index:
            parent = (index - 1) // 2
            if not heap[parent].priority < heap[index].priority:
                break
            self._swap_maximum(index, parent)
            index = parent

    def _minimum_down(self, index: int) -> None:
        heap = self._minimum
        while True:
            left, right = 2 * index + 1, 2 * index + 2
            smallest = index
            if left < len(heap) and heap[left].priority < heap[smallest].priority:
                smallest = left
            if right < len(heap) and heap[right].priority < heap[smallest].priority:
                smallest = right
            if smallest == index:
                return
            self._swap_minimum(index, smallest)
            index = smallest

    def _maximum_down(self, index: int) -> None:
        heap = self._maximum
        while True:
            left, right = 2 * index + 1, 2 * index + 2
            largest = index
            if left < len(heap) and heap[largest].priority < heap[left].priority:
                largest = left
            if right < len(heap) and heap[largest].priority < heap[right].priority:
                largest = right
            if largest == index:
                return
            self._swap_maximum(index, largest)
            index = largest

    def push(self, priority: float, node: Node) -> None:
        """Insert an alternative, replacing the maximum only if strictly better."""
        if self.capacity == 0:
            return
        size = len(self._minimum)
        if size < self.capacity:
            entry = _Entry(priority, node, size, size)
            self._minimum.append(entry)
            self._maximum.append(entry)
            self._minimum_up(size)
            self._maximum_up(size)
        elif priority < self._maximum[0].priority:
            index = self._maximum[0].minimum_index
            entry = _Entry(priority, node, index, 0)
            self._minimum[index] = entry
            self._maximum[0] = entry
            self._minimum_up(index)
            self._maximum_down(0)

    def pop(self, *, shrink: bool = False) -> tuple[float, Node]:
        """Remove the minimum using source swaps, optionally shrinking capacity."""
        selected = self._minimum[0]
        if len(self._minimum) == 1:
            self._minimum.pop()
            self._maximum.pop()
        else:
            index = selected.maximum_index
            self._swap_minimum(0, len(self._minimum) - 1)
            self._minimum.pop()
            self._minimum_down(0)
            if index == len(self._maximum) - 1:
                self._maximum.pop()
            else:
                self._swap_maximum(index, len(self._maximum) - 1)
                self._maximum.pop()
                if index and (
                    self._maximum[(index - 1) // 2].priority
                    < self._maximum[index].priority
                ):
                    self._maximum_up(index)
                else:
                    self._maximum_down(index)
        if shrink and self.capacity:
            self.capacity -= 1
        return selected.priority, selected.node
