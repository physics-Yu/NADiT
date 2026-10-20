"""Bind constructed staging templates to actual AOD resource requirements."""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise

from ..device import AOD, Position
from ..state import COORDINATE_TOLERANCE, AtomLayout, AtomState
from .occupancy import Occupancy
from .rules import MoveRequest, _partition
from .staging import StageEvent, generate_stages
from .staging_geometry import StagingGeometry


@dataclass(frozen=True, slots=True)
class _StagePlan:
    events: tuple[StageEvent, ...]
    fits: bool
    footprint: tuple[tuple[tuple[float, ...], tuple[float, ...]], ...]


class StagingPlans:
    """Cache one source-template construction for each ordered movement group.

    Args:
        layout: SLM entry states for this layout transition.
        destinations: Receiving SLM states indexed by atom identity.
        geometry: Source and target lattice descriptors.
        aod: Configured workspace, line spacing, and row/column capacities.

    Resource binding and the active-grid footprint use the template's carried
    coordinates. Accepted events are reused by Atom IR emission.
    """

    def __init__(
        self,
        layout: AtomLayout,
        destinations: Mapping[int, AtomState],
        geometry: StagingGeometry,
        aod: AOD,
    ) -> None:
        self.layout = layout
        self.destinations = destinations
        self.geometry = geometry
        self.aod = aod
        self._plans: dict[tuple[int, ...], _StagePlan] = {}

    def events(self, moves: tuple[MoveRequest, ...]) -> tuple[StageEvent, ...]:
        """Return the cached source-template events for the ordered group."""
        return self._plan(moves).events

    def fits(self, moves: tuple[MoveRequest, ...]) -> bool:
        """Return whether every constructed active grid fits the configured AOD."""
        return self._plan(moves).fits

    def blockers(self, moves: tuple[MoveRequest, ...], occupied: Occupancy) -> set[int]:
        """Return other atoms touched by the constructed active-grid footprint."""
        group = {move.atom_id for move in moves}
        return {
            atom
            for xs, ys in self._plan(moves).footprint
            for atom in occupied.at_grid(xs, ys)
            if atom not in group
        }

    def _plan(self, moves: tuple[MoveRequest, ...]) -> _StagePlan:
        key = tuple(move.atom_id for move in moves)
        if key not in self._plans:
            events = generate_stages(
                moves, self.layout, self.destinations, self.geometry
            )
            self._plans[key] = self._bind_resources(events, moves)
        return self._plans[key]

    def _bind_resources(
        self, events: tuple[StageEvent, ...], moves: tuple[MoveRequest, ...]
    ) -> _StagePlan:
        _, rows = _partition(tuple(move.source.y for move in moves))
        _, columns = _partition(tuple(move.source.x for move in moves))
        lines = {
            move.atom_id: (row, column)
            for move, row, column in zip(moves, rows, columns, strict=True)
        }
        carried: dict[int, Position] = {}
        footprint: list[tuple[tuple[float, ...], tuple[float, ...]]] = []
        fits = True
        bounds = self.aod.workspace
        for event in events:
            if event.kind == "store":
                for atom, _ in event.positions:
                    del carried[atom]
            else:
                carried.update(event.positions)
            active_rows = {lines[atom][0]: point.y for atom, point in carried.items()}
            active_columns = {
                lines[atom][1]: point.x for atom, point in carried.items()
            }
            xs = tuple(sorted(active_columns.values()))
            ys = tuple(sorted(active_rows.values()))
            if carried:
                footprint.append((xs, ys))
            fits = fits and (
                self._axis_fits(ys, bounds.y_min, bounds.y_max, self.aod.max_rows)
                and self._axis_fits(
                    xs, bounds.x_min, bounds.x_max, self.aod.max_columns
                )
            )
        return _StagePlan(events, fits, tuple(footprint))

    def _axis_fits(
        self,
        coordinates: tuple[float, ...],
        lower: float,
        upper: float,
        capacity: int | None,
    ) -> bool:
        tolerance = COORDINATE_TOLERANCE
        return (
            (capacity is None or len(coordinates) <= capacity)
            and all(
                lower - tolerance <= value <= upper + tolerance for value in coordinates
            )
            and all(
                right - left >= self.aod.min_spacing - tolerance
                for left, right in pairwise(coordinates)
            )
        )
