"""Distance-ordered strict grouping with staged row/column transfers."""

from dataclasses import dataclass
from math import hypot

from ..atom_ir import AtomStep
from ..contracts import AtomFragment
from ..device import Device, Position
from ..state import AODHolder, AtomLayout, AtomState, SLMHolder
from .occupancy import Occupancy
from .pair import _without_empty_grids
from .rules import MoveRequest, StrictRules, build_conflict_map
from .sequential import SequentialTransport
from .staged_emission import emit_stages
from .staged_policy import staging_regions
from .staging import generate_stages
from .staging_geometry import StagingGeometry
from .strict import StrictTransport, _requests


@dataclass(frozen=True, slots=True)
class StagedStrictTransport:
    """Construct strict rearrangements using the reference staged transfers.

    Args:
        aod_id: Flexible AOD preserving active row/column order.
        geometry: Optional explicit SLM lattice geometry. By default, regular
            grids and isolated gate-pair arrays are inferred from Device sites.

    Direct strict transport handles regions without admitted staging lanes.
    AOD-held boundary states use the existing singleton readout transport.
    """

    aod_id: str
    geometry: StagingGeometry | None = None

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Return staged Atom IR and the constructed final state.

        Args:
            layout: Current positions, holders, and qubit binding.
            target_layout: Selected endpoints for the same atoms and binding.
            device: Fixed sites, AOD limits, and action timing.

        Returns:
            Complete LOAD/MOVE/STORE steps, including intermediate offsets.

        Raises:
            ValueError: If endpoints are inconsistent or the available
                constructors cannot resolve a transport dependency.
        """
        current = _without_empty_grids(layout)
        target = _without_empty_grids(target_layout)
        if (
            any(
                isinstance(atom.holder, AODHolder)
                for boundary in (current, target)
                for atom in boundary.atoms
            )
            or current.aod_grids
            or target.aod_grids
        ):
            return SequentialTransport(self.aod_id).construct(current, target, device)
        rules = StrictRules(device.aod(self.aod_id), device.timing)
        destinations = {atom.id: atom for atom in target.atoms}
        requests = _requests(
            current,
            target,
            {atom.id: atom for atom in current.atoms},
            destinations,
            device,
            rules,
        )
        if not requests:
            return AtomFragment((), current)
        geometry = self.geometry or StagingGeometry.from_device(device, self.aod_id)
        regions = staging_regions(device, rules.aod, geometry)
        region_pairs = _region_pairs(current, destinations, geometry, regions)
        if not any(move.atom_id in region_pairs for move in requests):
            return StrictTransport(self.aod_id).construct(current, target, device)
        ordered = tuple(
            sorted(
                requests,
                key=lambda move: (
                    -hypot(
                        move.target.x - move.source.x, move.target.y - move.source.y
                    ),
                    move.atom_id,
                ),
            )
        )
        selector = _StagedGroups(
            rules,
            {move.atom_id: move for move in ordered},
            build_conflict_map(ordered, rules),
            region_pairs,
            Occupancy({atom.id: atom.position for atom in current.atoms}),
            {
                move.atom_id
                for move in ordered
                if isinstance((holder := destinations[move.atom_id].holder), SLMHolder)
                and holder.site_id in geometry.sites
                and geometry.sites[holder.site_id].kind != "storage"
            },
            {move.target: move.atom_id for move in ordered},
        )
        pending = set(selector.requests)
        steps: list[AtomStep] = []
        while pending:
            group = selector.select(pending)
            if not group:
                remaining = StrictTransport(self.aod_id).construct(
                    current, target, device
                )
                return AtomFragment(
                    tuple(steps) + remaining.steps, remaining.exit_layout
                )
            events = generate_stages(group, current, destinations, geometry)
            fragment = emit_stages(events, group, current, destinations, device, rules)
            steps.extend(fragment.steps)
            current = fragment.exit_layout
            for move in group:
                selector.occupied.remove(move.atom_id)
                pending.remove(move.atom_id)
            for move in group:
                selector.occupied.add(move.atom_id, move.target)
        return AtomFragment(tuple(steps), current)


def _region_pairs(
    layout: AtomLayout,
    destinations: dict[int, AtomState],
    geometry: StagingGeometry,
    regions: set[str],
) -> dict[int, tuple[str, str]]:
    pairs = {}
    for atom in layout.atoms:
        holder = atom.holder
        target_holder = destinations[atom.id].holder
        assert isinstance(holder, SLMHolder) and isinstance(target_holder, SLMHolder)
        source = geometry.sites.get(holder.site_id)
        target = geometry.sites.get(target_holder.site_id)
        if (
            source is not None
            and target is not None
            and source.zone_id in regions
            and target.zone_id in regions
        ):
            pairs[atom.id] = (source.zone_id, target.zone_id)
    return pairs


@dataclass
class _StagedGroups:
    rules: StrictRules
    requests: dict[int, MoveRequest]
    conflicts: dict[int, frozenset[int]]
    region_pairs: dict[int, tuple[str, str]]
    occupied: Occupancy
    gate_targets: set[int]
    target_atoms: dict[Position, int]

    def select(self, pending: set[int]) -> tuple[MoveRequest, ...]:
        for seed in self.requests:
            if seed not in pending or seed not in self.region_pairs:
                continue
            selected = self._admit({seed}, pending, self.region_pairs[seed])
            if selected is None:
                continue
            for candidate in self.requests:
                if candidate not in pending or candidate in selected:
                    continue
                enlarged = self._admit(
                    selected | {candidate}, pending, self.region_pairs[seed]
                )
                if enlarged is not None:
                    selected = enlarged
            return tuple(
                move for atom, move in self.requests.items() if atom in selected
            )
        return ()

    def _admit(
        self, selected: set[int], pending: set[int], regions: tuple[str, str]
    ) -> set[int] | None:
        selected = set(selected)
        while True:
            if any(
                self.region_pairs.get(atom) != regions
                or self.conflicts[atom] & selected
                for atom in selected
            ):
                return None
            moves = tuple(self.requests[atom] for atom in selected)
            if not self.rules.can_fit(moves):
                return None
            columns = {move.target.x for move in moves}
            rows = {move.target.y for move in moves}
            # The reference gate-column spread can park a third sparse column
            # on a physical trap. Admit at most two columns, or complete a
            # Cartesian block that aligns and releases all columns together.
            if (
                selected & self.gate_targets
                and len(rows) >= len(columns) > 2
                and len(selected) != len(rows) * len(columns)
            ):
                required = {
                    self.target_atoms.get(Position(x, y)) for x in columns for y in rows
                }
                if None in required or not required <= pending:
                    return None
                selected.update(atom for atom in required if atom is not None)
                continue
            occupants = (
                set().union(*(self.occupied.at(move.target) for move in moves))
                - selected
            )
            if not occupants:
                return selected
            if not occupants <= pending:
                return None
            selected.update(occupants)
