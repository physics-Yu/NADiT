"""Illumination over several spatially separated regions."""

from dataclasses import dataclass

from .device import Position, Region


@dataclass(frozen=True, slots=True)
class RegionSet:
    """An ordered union of regions; gaps between them remain unilluminated.

    Args:
        regions: Regions addressed by one simultaneous pulse.
    """

    regions: tuple[Region, ...]

    def contains(self, position: Position) -> bool:
        """Return whether a position is illuminated by any member region."""
        return any(region.contains(position) for region in self.regions)
