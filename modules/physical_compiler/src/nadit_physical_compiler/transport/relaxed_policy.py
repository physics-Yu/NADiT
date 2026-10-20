"""Bind relaxed groups to source geometry, actual resources, and occupancy."""

from ..device import Device
from ..state import AtomLayout, AtomState
from .occupancy import Occupancy
from .relaxed_groups import MovementGroups
from .relaxed_rules import RelaxedRules
from .rules import MoveRequest
from .staged import _region_pairs
from .staged_plan import StagingPlans
from .staged_policy import staging_regions
from .staging_geometry import StagingGeometry


class RelaxedAdmission:
    """Bind source-template plans to Device resources and vacant destinations.

    Args:
        layout: Initial atom positions and holders.
        destinations: Final atom states indexed by identity.
        device: Fixed sites and operation zones.
        rules: AOD geometry and endpoint capacity.
        geometry: Regular-array metadata used by the staged generator.
        plans: Shared cached stage construction and exact resource requirements.

    The callback uses the same stage plans later emitted as Atom IR. It does
    not run action checkers or replay.
    """

    def __init__(
        self,
        layout: AtomLayout,
        destinations: dict[int, AtomState],
        device: Device,
        rules: RelaxedRules,
        geometry: StagingGeometry,
        plans: StagingPlans,
    ) -> None:
        self.layout = layout
        self.rules = rules
        self.plans = plans
        regions = staging_regions(device, rules.aod, geometry)
        self.pairs = _region_pairs(layout, destinations, geometry, regions)

    def accepts(self, groups: MovementGroups) -> bool:
        """Return whether every proposed group has a legal constructive template."""
        occupied = Occupancy({atom.id: atom.position for atom in self.layout.atoms})
        for group in groups:
            if not self._shape_fits(group):
                return False
            atoms = {move.atom_id for move in group}
            if any(occupied.at(move.target) - atoms for move in group):
                return False
            if self.plans.blockers(group, occupied):
                return False
            for move in group:
                occupied.remove(move.atom_id)
            for move in group:
                occupied.add(move.atom_id, move.target)
        return True

    def _shape_fits(self, group: tuple[MoveRequest, ...]) -> bool:
        if not group or not self.rules.can_fit(group):
            return False
        if any(move.atom_id not in self.pairs for move in group):
            return False
        return self.plans.fits(group)
