"""An explicit catalogue of isolated SLM pairs under one shared CZ pulse."""

from dataclasses import dataclass
from itertools import combinations
from math import hypot
from typing import Self

from ..device import Device, Region, SLMSite


@dataclass(frozen=True, slots=True)
class GatePairCatalog:
    """Fixed pair positions and the region illuminated by a common pulse.

    Args:
        pairs: Ordered, isolated SLM site pairs.
        coverage: Shared CZ zone bounds.

    Build a device catalogue with from_device to check its geometry once.
    """

    pairs: tuple[tuple[SLMSite, SLMSite], ...]
    coverage: Region

    @property
    def capacity(self) -> int:
        """Return the number of simultaneous CZ pairs."""
        return len(self.pairs)

    @classmethod
    def from_device(
        cls, device: Device, zone_id: str, pairs: tuple[tuple[str, str], ...]
    ) -> Self:
        """Resolve and validate fixed pairs for one CZ zone.

        Args:
            device: Fixed traps and the CZ interaction-distance capability.
            zone_id: Zone whose full bounds define the shared pulse coverage.
            pairs: Ordered pairs of SLM site IDs.

        Returns:
            A catalogue retaining the device's site and region records.

        Raises:
            ValueError: If the zone, capability, or pair geometry is unsuitable.
            KeyError: If an SLM site ID is absent from the device.
        """
        zone = next((item for item in device.zones if item.id == zone_id), None)
        if zone is None or "cz" not in zone.operations:
            raise ValueError(f"Zone {zone_id!r} must permit CZ.")
        if device.cz is None or ("slm", "slm") not in device.cz.holder_pairs:
            raise ValueError("The catalogue requires an SLM-SLM CZ capability.")
        if not pairs:
            raise ValueError("A gate-pair catalogue must contain at least one pair.")
        resolved = tuple((device.site(a), device.site(b)) for a, b in pairs)
        sites = tuple(site for pair in resolved for site in pair)
        if len({site.id for site in sites}) != len(sites):
            raise ValueError("Gate-pair site IDs must be distinct.")
        if len({site.position for site in sites}) != len(sites):
            raise ValueError("Gate-pair site positions must be distinct.")
        if any(not zone.bounds.contains(site.position) for site in sites):
            raise ValueError(
                "Every gate-pair site must lie within the selected CZ zone."
            )
        radius = device.cz.radius_um
        if any(_distance(a, b) > radius for a, b in resolved):
            raise ValueError(
                "Each gate pair must lie within the CZ interaction radius."
            )
        for first, second in combinations(resolved, 2):
            if any(_distance(a, b) <= radius for a in first for b in second):
                raise ValueError("Sites from different pairs must not interact.")
        return cls(resolved, zone.bounds)


def _distance(first: SLMSite, second: SLMSite) -> float:
    return hypot(
        first.position.x - second.position.x, first.position.y - second.position.y
    )
