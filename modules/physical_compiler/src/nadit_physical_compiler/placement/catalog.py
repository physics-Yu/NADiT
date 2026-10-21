"""Isolated SLM pairs under one shared CZ pulse over one or several regions."""

from dataclasses import dataclass
from itertools import combinations
from math import hypot
from typing import Self

from ..coverage import RegionSet
from ..device import Device, Region, SLMSite


@dataclass(frozen=True, slots=True)
class GatePairCatalog:
    """Fixed pair positions and the region illuminated by a common pulse.

    Args:
        pairs: Ordered, isolated SLM site pairs.
        coverage: Shared CZ region or union of separate regions.
        array_groups: Optional paired-array groups, independent of illumination.

    Build a device catalogue with from_device to check its geometry once.
    """

    pairs: tuple[tuple[SLMSite, SLMSite], ...]
    coverage: Region | RegionSet
    array_groups: tuple[tuple[tuple[SLMSite, SLMSite], ...], ...] = ()

    @property
    def capacity(self) -> int:
        """Return the number of simultaneous CZ pairs."""
        return len(self.pairs)

    @property
    def regions(self) -> tuple[Region, ...]:
        """Return the separate illuminated regions in declaration order."""
        return (
            self.coverage.regions
            if isinstance(self.coverage, RegionSet)
            else (self.coverage,)
        )

    @property
    def region_pairs(self) -> tuple[tuple[tuple[SLMSite, SLMSite], ...], ...]:
        """Return declared pair arrays, even when illumination regions overlap."""
        return self.array_groups or (self.pairs,)

    @classmethod
    def from_pair_arrays(cls, device: Device) -> Self:
        """Build the default catalogue from the Device's declared pair arrays."""
        return cls.from_zones(
            device, tuple((array.zone, array.pairs) for array in device.pair_arrays)
        )

    @classmethod
    def from_zones(
        cls,
        device: Device,
        zones: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    ) -> Self:
        """Combine checked pair arrays from several CZ regions.

        Args:
            device: Source of region and interaction capabilities.
            zones: Ordered zone IDs and their paired SLM site IDs.

        Returns:
            One catalogue and the union covered by a simultaneous CZ pulse.

        Raises:
            ValueError: If regions share sites or different pairs interact.
        """
        catalogs = tuple(cls.from_device(device, name, pairs) for name, pairs in zones)
        pairs = tuple(pair for catalog in catalogs for pair in catalog.pairs)
        site_ids = [site.id for pair in pairs for site in pair]
        if not catalogs or len(set(site_ids)) != len(site_ids):
            raise ValueError("CZ zones must contain distinct catalogue sites.")
        assert device.cz is not None
        for first, second in combinations(catalogs, 2):
            if any(
                _distance(a, b) <= device.cz.radius_um
                for pair_a in first.pairs
                for pair_b in second.pairs
                for a in pair_a
                for b in pair_b
            ):
                raise ValueError("Sites from different CZ zones must not interact.")
        regions = tuple(
            dict.fromkeys(region for catalog in catalogs for region in catalog.regions)
        )
        return cls(
            pairs,
            regions[0] if len(regions) == 1 else RegionSet(regions),
            tuple(catalog.pairs for catalog in catalogs),
        )

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
