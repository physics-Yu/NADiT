"""Construct closed pickup groups and monotone readout layouts."""

from dataclasses import dataclass
from itertools import product
from math import floor, hypot

from ..contracts import GateLayer
from ..device import Device, Position, Region
from ..physical_ir import PhysicalDAG
from ..placement.pair import _intersection
from ..state import COORDINATE_TOLERANCE, AtomLayout, SLMHolder
from ..transport.occupancy import Occupancy
from ..transport.rules import MoveRequest, TransportRules, rules_for


@dataclass(frozen=True, slots=True)
class ReadoutBatch:
    """A closed source grid, readout endpoints, and native measurement identities."""

    moves: tuple[MoveRequest, ...]
    operation_ids: tuple[int, ...]


def plan_readout(
    dag: PhysicalDAG, layer: GateLayer, layout: AtomLayout, device: Device, aod_id: str
) -> tuple[ReadoutBatch, ...]:
    """Greedily group ready measurements; every batch returns to its entry sites.

    Source pickup closure includes only pending measurements. Target axes retain
    source order and sharing, fit the readout region, and avoid stationary traps.
    Raises ValueError when retained flexible AOD readout or a singleton placement
    is unavailable. The planner does not sample results or run full validators.
    """
    capability = device.readout
    aod = device.aod(aod_id)
    if (
        capability is None
        or not capability.retain_atoms
        or "aod" not in capability.holders
        or aod.motion != "flexible"
    ):
        raise ValueError("Batch readout requires retained flexible AOD readout.")
    if any(not isinstance(atom.holder, SLMHolder) for atom in layout.atoms):
        raise ValueError("Batch readout starts and ends with atoms in SLM sites.")
    if any(grid.rows or grid.columns for grid in layout.aod_grids):
        raise ValueError("Batch readout requires an initially inactive AOD grid.")
    operations = {
        layout.qubit_to_atom[dag.nodes[index].qubits[0]]: index
        for index in layer.operation_ids
    }
    if any(dag.nodes[index].name != "measure" for index in layer.operation_ids):
        raise ValueError("Batch readout accepts measurement layers only.")
    selector = _Selector(
        {atom: layout.atom(atom).position for atom in operations},
        Occupancy({atom.id: atom.position for atom in layout.atoms}),
        rules_for(aod, device.timing),
        tuple(
            bounds
            for zone in device.zones
            if "measure" in zone.operations
            if (bounds := _intersection(zone.bounds, aod.workspace)) is not None
        ),
        max(aod.min_spacing, capability.min_spacing_um),
        capability.max_atoms,
    )
    pending = set(operations)
    batches = []
    while pending:
        moves = selector.select(pending)
        batches.append(
            ReadoutBatch(moves, tuple(operations[move.atom_id] for move in moves))
        )
        pending.difference_update(move.atom_id for move in moves)
    return tuple(batches)


@dataclass
class _Selector:
    sources: dict[int, Position]
    occupied: Occupancy
    rules: TransportRules
    regions: tuple[Region, ...]
    spacing: float
    max_atoms: int | None

    def select(self, pending: set[int]) -> tuple[MoveRequest, ...]:
        for seed in self.sources:
            if seed not in pending:
                continue
            selected = self.close({seed}, pending)
            if selected is None:
                continue
            moves = self.place(selected)
            if moves is None:
                continue
            for candidate in self.sources:
                if candidate not in pending or candidate in selected:
                    continue
                enlarged = self.close(selected | {candidate}, pending)
                if enlarged is None:
                    continue
                trial = self.place(enlarged)
                if trial is not None:
                    selected, moves = enlarged, trial
            return moves
        raise ValueError("No ready measurement has a reachable readout placement.")

    def close(self, selected: set[int], pending: set[int]) -> set[int] | None:
        selected = set(selected)
        while True:
            if self.max_atoms is not None and len(selected) > self.max_atoms:
                return None
            identities = tuple(
                MoveRequest(atom, point, point)
                for atom, point in self.sources.items()
                if atom in selected
            )
            if not self.rules.can_fit(identities):
                return None
            grid, _, _ = self.rules.frames(identities)
            blockers = (
                self.occupied.at_grid(
                    tuple(c.coordinate for c in grid.columns),
                    tuple(r.coordinate for r in grid.rows),
                )
                - selected
            )
            if not blockers:
                return selected
            if not blockers <= pending:
                return None
            selected.update(blockers)

    def place(self, selected: set[int]) -> tuple[MoveRequest, ...] | None:
        identities = tuple(
            MoveRequest(atom, point, point)
            for atom, point in self.sources.items()
            if atom in selected
        )
        source, _, bindings = self.rules.frames(identities)
        options = []
        for region in self.regions:
            xs = _axis_options(
                tuple(c.coordinate for c in source.columns),
                region.x_min,
                region.x_max,
                self.spacing,
            )
            ys = _axis_options(
                tuple(r.coordinate for r in source.rows),
                region.y_min,
                region.y_max,
                self.spacing,
            )
            for columns, rows in product(xs, ys):
                if self.occupied.at_grid(columns, rows) - selected:
                    continue
                moves = tuple(
                    MoveRequest(
                        atom, self.sources[atom], Position(columns[column], rows[row])
                    )
                    for atom, row, column in bindings
                )
                if self.rules.can_fit(moves):
                    distance = max(
                        hypot(m.target.x - m.source.x, m.target.y - m.source.y)
                        for m in moves
                    )
                    options.append((distance, columns, rows, moves))
        return min(options, key=lambda item: item[:3])[3] if options else None


def _axis_options(
    source: tuple[float, ...], low: float, high: float, spacing: float
) -> tuple[tuple[float, ...], ...]:
    n = len(source)
    if (n - 1) * spacing > high - low + COORDINATE_TOLERANCE:
        return ()
    nearest: list[float] = []
    for i, value in enumerate(source):
        lower = nearest[-1] + spacing if nearest else low
        nearest.append(max(lower, min(value, high - (n - i - 1) * spacing)))
    return tuple(
        dict.fromkeys(
            (
                tuple(nearest),
                tuple(high - (n - i - 1) * spacing for i in range(n)),
                *(
                    tuple(low + (shift + i) * spacing for i in range(n))
                    for shift in range(
                        1
                        + max(
                            0,
                            floor(
                                (high - low - (n - 1) * spacing + COORDINATE_TOLERANCE)
                                / spacing
                            ),
                        )
                    )
                ),
            )
        )
    )
