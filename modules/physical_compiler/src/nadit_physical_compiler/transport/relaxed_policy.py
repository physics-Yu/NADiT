"""Constructive device admission for proposed relaxed rearrangement groups."""

from dataclasses import dataclass

from ..device import Device, Position, Region
from ..state import COORDINATE_TOLERANCE, AtomLayout, AtomState
from .occupancy import Occupancy
from .relaxed_groups import MovementGroups
from .relaxed_rules import RelaxedRules
from .rules import MoveRequest
from .staged import _region_pairs
from .staged_policy import staging_regions
from .staging_geometry import SiteStagingGeometry, StagingGeometry


@dataclass(frozen=True, slots=True)
class _RegionProfile:
    shape: SiteStagingGeometry
    bounds: Region
    foreign_sites: tuple[Position, ...]


class RelaxedAdmission:
    """Admit a routing through known lattice lanes and vacant destinations.

    Args:
        layout: Initial atom positions and holders.
        destinations: Final atom states indexed by identity.
        device: Fixed sites and operation zones.
        rules: AOD geometry and endpoint capacity.
        geometry: Regular-array metadata used by the staged generator.

    The callback works on movement requests before action generation. It does
    not generate, check, or replay atom actions.
    """

    def __init__(
        self,
        layout: AtomLayout,
        destinations: dict[int, AtomState],
        device: Device,
        rules: RelaxedRules,
        geometry: StagingGeometry,
    ) -> None:
        self.layout = layout
        self.rules = rules
        regions = staging_regions(device, rules.aod, geometry)
        self.pairs = _region_pairs(layout, destinations, geometry, regions)
        self.profiles: dict[str, _RegionProfile] = {}
        for zone in regions:
            sites = {
                site: item
                for site, item in geometry.sites.items()
                if item.zone_id == zone
            }
            shapes = tuple(sites.values())
            shape = shapes[0]
            self.profiles[zone] = _RegionProfile(
                shape,
                Region(
                    min(item.min_x for item in shapes) - shape.pitch_x,
                    shape.min_y - shape.pitch_y / 2,
                    max(item.max_x for item in shapes),
                    shape.max_y,
                ),
                tuple(
                    site.position for site in device.slm_sites if site.id not in sites
                ),
            )

    def accepts(self, groups: MovementGroups) -> bool:
        """Return whether every proposed group has a legal constructive template."""
        occupied = Occupancy({atom.id: atom.position for atom in self.layout.atoms})
        for group in groups:
            if not self._shape_fits(group):
                return False
            atoms = {move.atom_id for move in group}
            if any(occupied.at(move.target) - atoms for move in group):
                return False
            for move in group:
                occupied.remove(move.atom_id)
            for move in group:
                occupied.add(move.atom_id, move.target)
        return True

    def _shape_fits(self, group: tuple[MoveRequest, ...]) -> bool:
        if not group or not self.rules.can_fit(group):
            return False
        pair = self.pairs.get(group[0].atom_id)
        if pair is None or any(self.pairs.get(move.atom_id) != pair for move in group):
            return False
        source, target = (self.profiles[zone] for zone in pair)
        horizontal = dict(sorted((move.source.x, move.target.x) for move in group))
        vertical = dict(sorted((move.source.y, move.target.y) for move in group))
        row_ordered = list(vertical.values()) == sorted(vertical.values())
        column_ordered = list(horizontal.values()) == sorted(horizontal.values())
        rows, columns = len(vertical), len(horizontal)
        column_release = not (column_ordered and rows < columns)
        if column_release and target.shape.kind != "storage" and columns > 2:
            if not column_ordered or len(group) != rows * columns:
                return False
        if not row_ordered:
            pad = rows * source.shape.pitch_y
            bounds = Region(
                source.bounds.x_min,
                min(source.bounds.y_min, min(vertical) - pad),
                source.bounds.x_max,
                max(source.bounds.y_max, max(vertical) + pad),
            )
            if not self._corridor_fits(bounds, source):
                return False
        if not column_ordered:
            if target.shape.kind == "storage":
                pad = columns * target.shape.pitch_x
                left = min(horizontal.values()) - pad
                right = max(horizontal.values()) + pad
            else:
                # With two reversed gate columns the other column parks on
                # either x-2d or x-S-d before the first column is released.
                separation = target.shape.pair_separation
                offset = (target.shape.pitch_x - separation) / 3
                left = min(horizontal.values()) - max(2 * offset, separation + offset)
                right = target.bounds.x_max
            bounds = Region(
                min(target.bounds.x_min, left),
                target.bounds.y_min,
                max(target.bounds.x_max, right),
                target.bounds.y_max,
            )
            if not self._corridor_fits(bounds, target):
                return False
        return True

    def _corridor_fits(self, bounds: Region, profile: _RegionProfile) -> bool:
        workspace = self.rules.aod.workspace
        tolerance = COORDINATE_TOLERANCE
        return (
            workspace.x_min - tolerance <= bounds.x_min
            and workspace.y_min - tolerance <= bounds.y_min
            and bounds.x_max <= workspace.x_max + tolerance
            and bounds.y_max <= workspace.y_max + tolerance
            and not any(bounds.contains(point) for point in profile.foreign_sites)
        )
