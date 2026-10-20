"""Routing-aware placement records and the reference search proxy score."""

# Adapted from MQT QMAP HeuristicPlacer.cpp/.hpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from collections.abc import Iterator
from dataclasses import dataclass
from math import hypot, isfinite, sqrt
from typing import Literal

from ..device import Position, SLMSite
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
        use_window: Select source windows instead of the whole nearest array.
        window_min_width: Initial source window width in lattice columns.
        window_ratio: Window height divided by width.
        window_share: Candidate target count relative to the number of jobs.

    Raises:
        ValueError: If window dimensions or the candidate share are invalid.

    These parameters tune proxy scores, independently of Device timing.
    """

    deepening_factor: float = 0.01
    deepening_value: float = 0
    lookahead_factor: float = 0.4
    reuse_level: float = 5
    trials: int = 4
    queue_capacity: int = 100
    use_window: bool = True
    window_min_width: int = 16
    window_ratio: float = 1.0
    window_share: float = 0.8

    def __post_init__(self) -> None:
        if self.window_min_width < 0:
            raise ValueError("Window minimum width must be nonnegative.")
        if not isfinite(self.window_ratio) or self.window_ratio <= 0:
            raise ValueError("Window aspect ratio must be finite and positive.")
        if not isfinite(self.window_share) or self.window_share < 0:
            raise ValueError("Window share must be finite and nonnegative.")


@dataclass(frozen=True, slots=True)
class PlacementOption:
    """One job's atom-to-site assignment and already weighted lookahead cost."""

    assignments: tuple[tuple[int, SLMSite], ...]
    moves: tuple[MoveRequest, ...]
    lookahead_cost: float = 0
    score_distances: tuple[float, ...] | None = None

    @property
    def distances(self) -> tuple[float, ...]:
        """Return source score metadata, or geometric distances for generic jobs."""
        if self.score_distances is not None:
            return self.score_distances
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
    nearest_site: SLMSite | None = None


@dataclass(frozen=True, slots=True)
class PlacementCoordinates:
    """Full source/free-target coordinates, independent of candidate windows.

    Args:
        sources: Positions of all atoms involved in the placement jobs.
        targets: All free target sites used by source discretization.
        nearest_storage_columns: Resolved nearest storage column coordinates,
            including a nearest free column when the geometric column is full.
    """

    sources: tuple[Position, ...]
    targets: tuple[SLMSite, ...]
    nearest_storage_columns: tuple[float, ...] = ()


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
        coordinates: PlacementCoordinates | None = None,
    ) -> None:
        self.jobs, self.rules, self.config = jobs, rules, config
        moves = tuple(
            move for job in jobs for option in job.options for move in option.moves
        )
        sources = (
            coordinates.sources
            if coordinates is not None
            else tuple(move.source for move in moves)
        )
        targets = (
            tuple(site.position for site in coordinates.targets)
            if coordinates is not None
            else tuple(move.target for move in moves)
        )
        self.source_ranks = (
            _ranks(tuple(point.y for point in sources)),
            _ranks(tuple(point.x for point in sources)),
        )
        self.target_ranks = (
            _ranks(tuple(point.y for point in targets)),
            _ranks(tuple(point.x for point in targets)),
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
            if coordinates is not None:
                nearest_columns = [
                    self.target_ranks[1][column]
                    for column in coordinates.nearest_storage_columns
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
    coordinates: PlacementCoordinates | None = None,
) -> tuple[PlacementOption, ...]:
    """Select complete site assignments with routing-aware bounded IDS.

    Args:
        jobs: Placement jobs in the candidate generator's search order.
        rules: Shared strict geometry and capacity rules.
        config: Search budgets and heuristic parameters.
        stage: Select gate or storage axis scaling.
        coordinates: Full free-array coordinates for source score discretization.

    Returns:
        Chosen options in job order. No jobs produce an empty tuple.

    Raises:
        ValueError: If the search budget is invalid or no complete assignment is found.

    Scores use square-root distance proxies; Device timing is applied later
    when the selected layouts are translated into transport actions.
    """
    search = _Search(jobs, rules, config, stage, coordinates)
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
