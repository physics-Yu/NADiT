"""Routing-aware placement records and the reference search proxy score."""

# Adapted from MQT QMAP HeuristicPlacer.cpp/.hpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from collections.abc import Iterator
from dataclasses import dataclass
from math import hypot, sqrt
from typing import Literal

from ..device import SLMSite
from ..state import COORDINATE_TOLERANCE
from ..transport.rules import MoveRequest, StrictRules
from .ids import iterative_diving_search


@dataclass(frozen=True, slots=True)
class PlacementConfig:
    """Reference IDS budgets, lookahead weights, and deepening parameters.

    Args:
        deepening_factor: Weight of group displacement deviation.
        deepening_value: Offset added to deviation before weighting.
        lookahead_factor: Weight of the next partner's square-root distance.
        reuse_level: Preference subtracted from a stay option's partner cost.
        trials: Number of complete search goals to compare.
        queue_capacity: Base queue bound; IDS initially adds the trial count.

    These parameters tune proxy scores, independently of Device timing.
    """

    deepening_factor: float = 0.01
    deepening_value: float = 0
    lookahead_factor: float = 0.4
    reuse_level: float = 5
    trials: int = 4
    queue_capacity: int = 100


@dataclass(frozen=True, slots=True)
class PlacementOption:
    """One job's atom-to-site assignment and already weighted lookahead cost."""

    assignments: tuple[tuple[int, SLMSite], ...]
    moves: tuple[MoveRequest, ...]
    lookahead_cost: float = 0

    @property
    def distances(self) -> tuple[float, ...]:
        """Return Euclidean movement distances in request order."""
        return tuple(
            hypot(move.target.x - move.source.x, move.target.y - move.source.y)
            for move in self.moves
        )

    @property
    def distance(self) -> float:
        """Return the longest movement, or zero for a stay option."""
        return max(self.distances, default=0)

    @property
    def site_ids(self) -> frozenset[str]:
        """Return occupied target site identities, including stay sites."""
        return frozenset(site.id for _, site in self.assignments)


@dataclass(frozen=True, slots=True)
class PlacementJob:
    """Ordered alternatives for one gate or atom placement."""

    options: tuple[PlacementOption, ...]


@dataclass(frozen=True, slots=True)
class _Node:
    """Selected options and incrementally accumulated movement groups."""

    chosen: tuple[PlacementOption, ...] = ()
    occupied: frozenset[str] = frozenset()
    groups: tuple[tuple[MoveRequest, ...], ...] = ()
    max_distances: tuple[float, ...] = ()
    lookahead_cost: float = 0


class _Search:
    """Share incremental first-fit groups between search cost and heuristic."""

    def __init__(
        self,
        jobs: tuple[PlacementJob, ...],
        rules: StrictRules,
        config: PlacementConfig,
        stage: Literal["gate", "storage"],
    ) -> None:
        self.jobs, self.rules, self.config = jobs, rules, config
        moves = tuple(
            move for job in jobs for option in job.options for move in option.moves
        )
        self.source_ranks = (
            _ranks(tuple(move.source.y for move in moves)),
            _ranks(tuple(move.source.x for move in moves)),
        )
        self.target_ranks = (
            _ranks(tuple(move.target.y for move in moves)),
            _ranks(tuple(move.target.x for move in moves)),
        )
        ratios = tuple(
            len(set(target.values())) / len(set(source.values()))
            if source and target
            else 1.0
            for source, target in zip(self.source_ranks, self.target_ranks, strict=True)
        )
        column_scale = min(1.0, ratios[1])
        if stage == "storage":
            nearest_columns = [
                self.target_ranks[1][option.moves[0].target.x]
                for job in jobs
                if (option := next((item for item in job.options if item.moves), None))
                is not None
            ]
            nearest_span = max(nearest_columns, default=0) - min(
                nearest_columns, default=0
            )
            source_span = max(self.source_ranks[1].values(), default=0)
            expansion = nearest_span / source_span if source_span > 0 else 1.0
            column_scale = min(max(1.0, expansion), ratios[1])
        self.scale_factors = (min(1.0, ratios[0]), column_scale)
        self.mean_lookahead = tuple(
            sum(option.lookahead_cost for option in job.options) / len(job.options)
            if job.options
            else 0
            for job in jobs
        )

    def expand(self, node: _Node) -> Iterator[_Node]:
        """Extend the next job, assigning each movement to its first fitting group."""
        for option in self.jobs[len(node.chosen)].options:
            if not node.occupied.isdisjoint(option.site_ids):
                continue
            groups = list(node.groups)
            distances = list(node.max_distances)
            for move, distance in zip(option.moves, option.distances, strict=True):
                for index, group in enumerate(groups):
                    if all(
                        self.rules.compatible(existing, move) for existing in group
                    ) and self.rules.can_fit((*group, move)):
                        groups[index] = (*group, move)
                        distances[index] = max(distances[index], distance)
                        break
                else:
                    if not self.rules.can_fit((move,)):
                        break
                    groups.append((move,))
                    distances.append(distance)
            else:
                yield _Node(
                    (*node.chosen, option),
                    node.occupied | option.site_ids,
                    tuple(groups),
                    tuple(distances),
                    node.lookahead_cost + option.lookahead_cost,
                )

    @staticmethod
    def cost(node: _Node) -> float:
        """Sum group maximum-distance proxies and selected lookahead costs."""
        return (
            sum(sqrt(distance) for distance in node.max_distances) + node.lookahead_cost
        )

    def heuristic(self, node: _Node) -> float:
        """Apply the reference first-free-option and axis-deviation heuristic."""
        level = len(node.chosen)
        nearest = 0.0
        for job in self.jobs[level:]:
            for option in job.options:
                if node.occupied.isdisjoint(option.site_ids):
                    nearest = max(nearest, option.distance)
                    break
        placed = max(node.max_distances, default=0)
        remaining = len(self.jobs) - level
        return (
            max(0.0, sqrt(nearest) - sqrt(placed))
            + sum(self.mean_lookahead[level:])
            + self.config.deepening_factor
            * (self._deviation(node) + self.config.deepening_value)
            * remaining
        )

    def _deviation(self, node: _Node) -> float:
        """Sum population deviations over unique row and column assignments."""
        total = 0.0
        for group in node.groups:
            for axis, attribute in enumerate(("y", "x")):
                mapping = {
                    self.source_ranks[axis][
                        getattr(move.source, attribute)
                    ]: self.target_ranks[axis][getattr(move.target, attribute)]
                    for move in group
                }
                differences = [
                    target - self.scale_factors[axis] * source
                    for source, target in mapping.items()
                ]
                mean = sum(differences) / len(differences)
                total += sqrt(
                    sum((value - mean) ** 2 for value in differences) / len(differences)
                )
        return total


def _ranks(coordinates: tuple[float, ...]) -> dict[float, int]:
    """Discretize one axis using the transport rule's fixed-representative tolerance."""
    ranks: dict[float, int] = {}
    representative: float | None = None
    index = -1
    for value in sorted(set(coordinates)):
        if representative is None or value - representative > COORDINATE_TOLERANCE:
            representative = value
            index += 1
        ranks[value] = index
    return ranks


def search_placement(
    jobs: tuple[PlacementJob, ...],
    rules: StrictRules,
    config: PlacementConfig,
    *,
    stage: Literal["gate", "storage"] = "gate",
) -> tuple[PlacementOption, ...]:
    """Select complete site assignments with routing-aware bounded IDS.

    Args:
        jobs: Placement jobs in the candidate generator's search order.
        rules: Shared strict geometry and capacity rules.
        config: Search budgets and heuristic parameters.
        stage: Select gate or storage axis scaling.

    Returns:
        Chosen options in job order. No jobs produce an empty tuple.

    Raises:
        ValueError: If the search budget is invalid or no complete assignment is found.

    Scores use square-root distance proxies; Device timing is applied later
    when the selected layouts are translated into transport actions.
    """
    search = _Search(jobs, rules, config, stage)
    result = iterative_diving_search(
        _Node(),
        expand=search.expand,
        is_goal=lambda node: len(node.chosen) == len(jobs),
        cost=search.cost,
        heuristic=search.heuristic,
        trials=config.trials,
        queue_capacity=config.queue_capacity,
    )
    return result.chosen
