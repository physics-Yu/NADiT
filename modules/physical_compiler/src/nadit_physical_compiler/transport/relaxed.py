"""Compose reference relaxed group merging with device-admitted staging."""

from dataclasses import dataclass
from math import hypot

from ..atom_ir import AtomStep
from ..contracts import AtomFragment
from ..device import Device
from ..state import AODHolder, AtomLayout, AtomState
from .occupancy import Occupancy
from .pair import _without_empty_grids
from .relaxed_groups import MovementGroups, route_relaxed
from .relaxed_policy import RelaxedAdmission
from .relaxed_rules import RelaxedRules
from .rules import MoveRequest, StrictRules, build_conflict_map
from .staged import StagedStrictTransport, _region_pairs, _StagedGroups
from .staged_emission import emit_stages
from .staged_plan import StagingPlans
from .staged_policy import staging_regions
from .staging_geometry import StagingGeometry
from .strict import _requests


@dataclass(frozen=True, slots=True)
class RelaxedTransport:
    """Merge strict batches when the reference offset proxy and Device permit.

    Args:
        aod_id: Order-preserving AOD used by each staged MOVE.
        geometry: Optional explicit SLM lattice geometry.
        prefer_split: Reference merge threshold, default 1.0. Larger values
            retain more strict groups; zero removes the proxy-cost threshold.
    """

    aod_id: str
    geometry: StagingGeometry | None = None
    prefer_split: float = 1.0

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Construct one layout transition with accepted relaxed redistributions.

        Args:
            layout: Current atom states and qubit binding.
            target_layout: Selected endpoints for those atoms.
            device: Geometry, line capacity, transfer times, and movement timing.

        Returns:
            Existing Atom IR steps and the constructed final layout.

        Raises:
            ValueError: If the available transport constructors cannot realize
                the endpoints or their occupancy dependencies.

        Cost or device rejection keeps the strict plan. No alternative action
        programs are generated for selection, and full checkers stay in tests.
        """
        current = _without_empty_grids(layout)
        target = _without_empty_grids(target_layout)
        fallback = StagedStrictTransport(self.aod_id, self.geometry)
        if (
            current.aod_grids
            or target.aod_grids
            or any(
                isinstance(atom.holder, AODHolder)
                for boundary in (current, target)
                for atom in boundary.atoms
            )
        ):
            return fallback.construct(current, target, device)
        strict = StrictRules(device.aod(self.aod_id), device.timing)
        destinations = {atom.id: atom for atom in target.atoms}
        moves = _requests(
            current,
            target,
            {atom.id: atom for atom in current.atoms},
            destinations,
            device,
            strict,
        )
        if not moves:
            return AtomFragment((), current)
        geometry = self.geometry or StagingGeometry.from_device(device, self.aod_id)
        plans = StagingPlans(current, destinations, geometry, strict.aod)
        initial = _strict_batches(
            current, destinations, device, strict, geometry, moves, plans
        )
        if initial is None:
            return fallback.construct(current, target, device)
        relaxed = RelaxedRules(strict.aod, strict.timing)
        admission = RelaxedAdmission(
            current, destinations, device, relaxed, geometry, plans
        )
        groups = route_relaxed(
            moves,
            relaxed,
            prefer_split=self.prefer_split,
            initial_groups=initial,
            admit=admission.accepts,
        )
        steps: list[AtomStep] = []
        for group in groups:
            events = plans.events(group)
            fragment = emit_stages(events, group, current, destinations, device, strict)
            steps.extend(fragment.steps)
            current = fragment.exit_layout
        return AtomFragment(tuple(steps), current)


def _strict_batches(
    layout: AtomLayout,
    destinations: dict[int, AtomState],
    device: Device,
    rules: StrictRules,
    geometry: StagingGeometry,
    moves: tuple[MoveRequest, ...],
    plans: StagingPlans,
) -> MovementGroups | None:
    regions = staging_regions(device, rules.aod, geometry)
    pairs = _region_pairs(layout, destinations, geometry, regions)
    if any(move.atom_id not in pairs for move in moves):
        return None
    ordered = tuple(
        sorted(
            moves,
            key=lambda move: (
                hypot(move.target.x - move.source.x, move.target.y - move.source.y),
                move.atom_id,
            ),
            reverse=True,
        )
    )
    selector = _StagedGroups(
        rules,
        {move.atom_id: move for move in ordered},
        build_conflict_map(ordered, rules),
        pairs,
        Occupancy({atom.id: atom.position for atom in layout.atoms}),
        plans.fits,
        plans.blockers,
    )
    pending = set(selector.requests)
    groups = []
    while pending:
        group = selector.select(pending)
        if not group:
            return None
        groups.append(group)
        for move in group:
            selector.occupied.remove(move.atom_id)
            pending.remove(move.atom_id)
        for move in group:
            selector.occupied.add(move.atom_id, move.target)
    return tuple(groups)
