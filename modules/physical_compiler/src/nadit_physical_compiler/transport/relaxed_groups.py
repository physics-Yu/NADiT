"""Reference strict grouping followed by transactional relaxed donor merges."""

# Adapted from MQT QMAP IndependentSetRouter.cpp/.hpp (MIT), revision
# cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d.
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from collections.abc import Callable
from dataclasses import dataclass, field
from math import cbrt, hypot, inf

from .relaxed_rules import (
    RelaxedRules,
    _sub_cube_roots_cubed,
    _sum_cube_roots_cubed,
)
from .rules import MoveRequest

type MovementGroups = tuple[tuple[MoveRequest, ...], ...]
type _ConflictGraph = dict[int, dict[int, float | None]]


@dataclass
class _Group:
    moves: list[MoveRequest] = field(default_factory=list)
    maximum_distance: float = 0
    conflicts: dict[int, float | None] = field(default_factory=dict)

    def append(
        self, move: MoveRequest, distances: dict[int, float], graph: _ConflictGraph
    ) -> None:
        self.moves.append(move)
        self.maximum_distance = max(self.maximum_distance, distances[move.atom_id])
        for neighbor, incoming in graph[move.atom_id].items():
            if neighbor not in self.conflicts:
                self.conflicts[neighbor] = incoming
            else:
                existing = self.conflicts[neighbor]
                if existing is not None:
                    self.conflicts[neighbor] = (
                        max(existing, incoming) if incoming is not None else None
                    )


def route_relaxed(
    moves: tuple[MoveRequest, ...],
    rules: RelaxedRules,
    *,
    prefer_split: float = 1.0,
    initial_groups: MovementGroups | None = None,
    admit: Callable[[MovementGroups], bool] | None = None,
) -> MovementGroups:
    """Merge strict groups using the public router's cubed-offset cost.

    Args:
        moves: Requests with distinct atom IDs.
        rules: Pair compatibility and endpoint capacity.
        prefer_split: Source merge threshold; larger values favor separate groups.
        initial_groups: Optional already legal strict grouping covering moves.
            Otherwise requests are grouped by descending distance and atom ID.
        admit: Optional construction-policy callback receiving the entire
            proposed routing once per cost- and capacity-qualified donor merge.

    Returns:
        The committed groups. Rejected proposals leave all groups unchanged.
        Python appends donor atoms in their existing order; the reference C++
        commits from an unordered_map and does not specify that internal order.

    Raises:
        ValueError: If a request cannot fit the AOD when initial groups are built.

    This function constructs no atom actions and calls no state validators.
    """
    distances = {
        move.atom_id: hypot(
            move.target.x - move.source.x, move.target.y - move.source.y
        )
        for move in moves
    }
    graph = _conflicts(moves, rules)
    initial = (
        initial_groups
        if initial_groups is not None
        else _strict_groups(moves, distances, graph, rules)
    )
    groups: list[_Group] = []
    for batch in initial:
        group = _Group()
        for move in batch:
            group.append(move, distances, graph)
        groups.append(group)
    donor_index = len(groups) - 1
    while donor_index >= 0:
        assignment = _donor_assignment(groups, donor_index, distances, rules)
        if assignment is not None:
            receivers, cubed_cost = assignment
            if groups[donor_index].maximum_distance > prefer_split * cubed_cost:
                proposal = _proposal(groups, donor_index, receivers)
                if all(rules.can_fit(group) for group in proposal) and (
                    admit is None or admit(proposal)
                ):
                    for move, receiver in zip(
                        groups[donor_index].moves, receivers, strict=True
                    ):
                        groups[receiver].append(move, distances, graph)
                    del groups[donor_index]
        donor_index -= 1
    return tuple(tuple(group.moves) for group in groups)


def _conflicts(moves: tuple[MoveRequest, ...], rules: RelaxedRules) -> _ConflictGraph:
    graph: _ConflictGraph = {move.atom_id: {} for move in moves}
    for index, first in enumerate(moves):
        for second in moves[index + 1 :]:
            result = rules.compatibility(first, second)
            if result.status != "strict":
                graph[first.atom_id][second.atom_id] = result.merge_cost
                graph[second.atom_id][first.atom_id] = result.merge_cost
    return graph


def _strict_groups(
    moves: tuple[MoveRequest, ...],
    distances: dict[int, float],
    graph: _ConflictGraph,
    rules: RelaxedRules,
) -> MovementGroups:
    remaining = sorted(
        moves, key=lambda move: (distances[move.atom_id], move.atom_id), reverse=True
    )
    groups: list[tuple[MoveRequest, ...]] = []
    while remaining:
        selected: list[MoveRequest] = []
        blocked: set[int] = set()
        postponed: list[MoveRequest] = []
        for move in remaining:
            candidate = tuple((*selected, move))
            if move.atom_id not in blocked and rules.can_fit(candidate):
                selected.append(move)
                blocked.update(graph[move.atom_id])
            else:
                postponed.append(move)
        if not selected:
            raise ValueError("A movement endpoint cannot fit the AOD workspace.")
        groups.append(tuple(selected))
        remaining = postponed
    return tuple(groups)


def _donor_assignment(
    groups: list[_Group],
    donor_index: int,
    distances: dict[int, float],
    rules: RelaxedRules,
) -> tuple[tuple[int, ...], float] | None:
    receivers: list[int] = []
    total_cost = 0.0
    total_cubed = 0.0
    for move in groups[donor_index].moves:
        best_receiver: int | None = None
        best_cubed = inf
        for index in range(len(groups) - 1, -1, -1):
            if index == donor_index:
                continue
            group = groups[index]
            if not rules.can_fit(tuple((*group.moves, move))):
                continue
            if (
                move.atom_id not in group.conflicts
                and group.maximum_distance >= distances[move.atom_id]
            ):
                best_receiver, best_cubed = index, 0.0
                break
            cost = _insertion_cost(move.atom_id, group, distances)
            if cost is None:
                continue
            if cost < best_cubed:
                best_receiver, best_cubed = index, cost
        if best_receiver is None:
            return None
        receivers.append(best_receiver)
        cost_root = cbrt(best_cubed)
        total_cubed = (
            best_cubed
            + total_cubed
            + 3 * cost_root * total_cost * (cost_root + total_cost)
        )
        total_cost += cost_root
    return tuple(receivers), total_cubed


def _insertion_cost(
    atom_id: int, group: _Group, distances: dict[int, float]
) -> float | None:
    distance = distances[atom_id]
    if atom_id in group.conflicts:
        conflict = group.conflicts[atom_id]
        if conflict is None:
            return None
        if group.maximum_distance >= distance:
            return conflict
        return _sum_cube_roots_cubed(
            _sub_cube_roots_cubed(distance, group.maximum_distance), conflict
        )
    if group.maximum_distance >= distance:
        return 0.0
    return _sub_cube_roots_cubed(distance, group.maximum_distance)


def _proposal(
    groups: list[_Group], donor_index: int, receivers: tuple[int, ...]
) -> MovementGroups:
    additions: dict[int, list[MoveRequest]] = {}
    for move, receiver in zip(groups[donor_index].moves, receivers, strict=True):
        additions.setdefault(receiver, []).append(move)
    return tuple(
        tuple((*group.moves, *additions.get(index, ())))
        for index, group in enumerate(groups)
        if index != donor_index
    )
