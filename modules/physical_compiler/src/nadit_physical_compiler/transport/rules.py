"""Shared transport geometry and timing, independent of layout occupancy."""

from dataclasses import dataclass
from itertools import pairwise
from math import hypot
from typing import Protocol

from ..device import AOD, Position, Timing
from ..state import COORDINATE_TOLERANCE, AODGrid, AODLine

type _Axis = tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class MoveRequest:
    """One atom's source and destination, in micrometers."""

    atom_id: int
    source: Position
    target: Position


class TransportRules(Protocol):
    """Motion compatibility shared by placement scores and transport batches."""

    @property
    def aod(self) -> AOD:
        """Return the device geometry and motion model."""
        ...

    @property
    def timing(self) -> Timing:
        """Return the transfer and distance-based movement timing."""
        ...

    def compatible(self, first: MoveRequest, second: MoveRequest) -> bool:
        """Return whether the requests can share one transport batch."""
        ...

    def can_fit(self, moves: tuple[MoveRequest, ...]) -> bool:
        """Return whether compatible requests fit workspace, spacing, and capacity."""
        ...

    def frames(
        self, moves: tuple[MoveRequest, ...]
    ) -> tuple[AODGrid, AODGrid, tuple[tuple[int, int, int], ...]]:
        """Return endpoint grids and atom-row-column bindings for a batch."""
        ...

    def duration_us(self, moves: tuple[MoveRequest, ...]) -> float:
        """Return the complete LOAD, MOVE, and STORE duration of a batch."""
        ...


@dataclass(frozen=True, slots=True)
class StrictRules:
    """Geometry and timing for a common LOAD, MOVE, and STORE sequence.

    Args:
        aod: Flexible AOD whose active row and column order must be preserved.
        timing: Transfer durations and distance-based movement timing.
    """

    aod: AOD
    timing: Timing

    def compatible(self, first: MoveRequest, second: MoveRequest) -> bool:
        """Return whether both axis relations and endpoint spacings are preserved.

        Occupancy and whole-batch capacity are handled separately.
        """
        return all(
            _axis_compatible(source, target, self.aod.min_spacing)
            for source, target in (
                (first.source.x - second.source.x, first.target.x - second.target.x),
                (first.source.y - second.source.y, first.target.y - second.target.y),
            )
        )

    def can_fit(self, moves: tuple[MoveRequest, ...]) -> bool:
        """Check workspace, line counts, and line spacing in O(k log k).

        Args:
            moves: Requests already pairwise compatible under the conflict map.

        Returns:
            Whether their endpoint grids fit the AOD. Empty batches fit.
        """
        bounds = self.aod.workspace
        tolerance = COORDINATE_TOLERANCE
        for move in moves:
            for point in (move.source, move.target):
                if not (
                    bounds.x_min - tolerance <= point.x <= bounds.x_max + tolerance
                    and bounds.y_min - tolerance <= point.y <= bounds.y_max + tolerance
                ):
                    return False
        rows, columns = _axes(moves)
        for (before, after, _), capacity in (
            (rows, self.aod.max_rows),
            (columns, self.aod.max_columns),
        ):
            for coordinates in (before, after):
                if capacity is not None and len(coordinates) > capacity:
                    return False
                if any(
                    right - left < self.aod.min_spacing - tolerance
                    for left, right in pairwise(coordinates)
                ):
                    return False
        return True

    def frames(
        self, moves: tuple[MoveRequest, ...]
    ) -> tuple[AODGrid, AODGrid, tuple[tuple[int, int, int], ...]]:
        """Return endpoint grids and (atom, row, column) loading bindings.

        Args:
            moves: Pairwise-compatible requests. Axis IDs follow sorted source
                coordinates; bindings retain request order.

        Returns:
            Source grid, target grid with the same line identities, and bindings.
            Nearby coordinates share a fixed representative within tolerance.
        """
        rows, columns = _axes(moves)
        source = AODGrid(
            self.aod.id,
            tuple(AODLine(index, value) for index, value in enumerate(rows[0])),
            tuple(AODLine(index, value) for index, value in enumerate(columns[0])),
        )
        target = AODGrid(
            self.aod.id,
            tuple(AODLine(index, value) for index, value in enumerate(rows[1])),
            tuple(AODLine(index, value) for index, value in enumerate(columns[1])),
        )
        bindings = tuple(
            (move.atom_id, row, column)
            for move, row, column in zip(moves, rows[2], columns[2], strict=True)
        )
        return source, target, bindings

    def duration_us(self, moves: tuple[MoveRequest, ...]) -> float:
        """Return load plus longest movement plus store time; empty means zero."""
        if not moves:
            return 0.0
        distance = max(
            hypot(move.target.x - move.source.x, move.target.y - move.source.y)
            for move in moves
        )
        return (
            self.timing.load_us
            + self.timing.move_duration(distance)
            + self.timing.store_us
        )


@dataclass(frozen=True, slots=True)
class RigidRules(StrictRules):
    """Preserve source-axis sharing and translate all lines by one vector.

    Capacity, endpoint frames, and timing use the same calculations as flexible
    transport; compatibility additionally requires equal displacement.
    """

    def compatible(self, first: MoveRequest, second: MoveRequest) -> bool:
        """Return whether both requests share a legal rigid translation."""
        return (
            abs((first.target.x - first.source.x) - (second.target.x - second.source.x))
            <= COORDINATE_TOLERANCE
            and abs(
                (first.target.y - first.source.y) - (second.target.y - second.source.y)
            )
            <= COORDINATE_TOLERANCE
            and StrictRules.compatible(self, first, second)
        )


def rules_for(aod: AOD, timing: Timing) -> TransportRules:
    """Select the device's motion rules for placement and transport.

    Args:
        aod: AOD workspace, line capacities, and motion model.
        timing: Transfer durations and distance-based movement timing.

    Returns:
        Rigid translation rules or the existing flexible strict rules.
    """
    return (
        RigidRules(aod, timing) if aod.motion == "rigid" else StrictRules(aod, timing)
    )


def build_conflict_map(
    moves: tuple[MoveRequest, ...], rules: TransportRules
) -> dict[int, frozenset[int]]:
    """Build symmetric motion conflicts, retaining isolated atom IDs.

    Args:
        moves: Movement requests with distinct atom identities.
        rules: Pair compatibility shared with placement and transport.

    Returns:
        Atom IDs mapped to incompatible request IDs. Occupancy and batch
        capacity do not add edges.
    """
    neighbors: dict[int, set[int]] = {move.atom_id: set() for move in moves}
    for index, first in enumerate(moves):
        for second in moves[index + 1 :]:
            if not rules.compatible(first, second):
                neighbors[first.atom_id].add(second.atom_id)
                neighbors[second.atom_id].add(first.atom_id)
    return {atom: frozenset(adjacent) for atom, adjacent in neighbors.items()}


def _axis_compatible(source_delta: float, target_delta: float, spacing: float) -> bool:
    source_shared = abs(source_delta) <= COORDINATE_TOLERANCE
    target_shared = abs(target_delta) <= COORDINATE_TOLERANCE
    if source_shared or target_shared:
        return source_shared and target_shared
    return (source_delta < 0) == (target_delta < 0) and min(
        abs(source_delta), abs(target_delta)
    ) >= spacing - COORDINATE_TOLERANCE


def _partition(values: tuple[float, ...]) -> tuple[tuple[float, ...], tuple[int, ...]]:
    representatives: list[float] = []
    assignments = [0] * len(values)
    for index in sorted(range(len(values)), key=values.__getitem__):
        value = values[index]
        if not representatives or value - representatives[-1] > COORDINATE_TOLERANCE:
            representatives.append(value)
        assignments[index] = len(representatives) - 1
    return tuple(representatives), tuple(assignments)


def _axes(moves: tuple[MoveRequest, ...]) -> tuple[_Axis, _Axis]:
    source_rows, row_ids = _partition(tuple(move.source.y for move in moves))
    target_rows, _ = _partition(tuple(move.target.y for move in moves))
    source_columns, column_ids = _partition(tuple(move.source.x for move in moves))
    target_columns, _ = _partition(tuple(move.target.x for move in moves))
    return (
        (source_rows, target_rows, row_ids),
        (source_columns, target_columns, column_ids),
    )
