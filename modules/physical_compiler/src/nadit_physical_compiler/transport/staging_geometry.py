"""Recover regular SLM-array geometry needed by staged transport offsets."""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, Self

from ..device import Device, Position, SLMSite
from ..state import COORDINATE_TOLERANCE

type SiteKind = Literal["storage", "left", "right"]


@dataclass(frozen=True, slots=True)
class SiteStagingGeometry:
    """The SLM subarray containing a site, in micrometers.

    max_x and max_y are one lattice pitch beyond the final column and row,
    matching the bounds used by the reference rearrangement generator.
    """

    kind: SiteKind
    pitch_x: float
    pitch_y: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    pair_separation: float
    zone_id: str


@dataclass(frozen=True, slots=True)
class StagingGeometry:
    """Explicit per-site array geometry shared by a staged transport instance.

    Args:
        sites: Recognized SLM site IDs and their subarray geometry. Callers may
            provide this mapping directly when fixed sites omit lattice pitches.
        coordinate_quantum: Optional coordinate unit for the source generator's
            integer divisions. None retains continuous geometry; 1 uses the
            original micrometre integer arithmetic.
    """

    sites: Mapping[str, SiteStagingGeometry]
    coordinate_quantum: float | None = None

    def __post_init__(self) -> None:
        if self.coordinate_quantum is not None and self.coordinate_quantum <= 0:
            raise ValueError("Staging coordinate quantum must be positive.")

    @classmethod
    def from_device(cls, device: Device, aod_id: str) -> Self:
        """Infer regular storage grids and horizontal CZ-pair arrays.

        Args:
            device: Fixed sites and operation zones.
            aod_id: AOD supplying the singleton-axis pitch fallback.

        Returns:
            Geometry for recognized sites only. Unsupported or ambiguous
            patterns are omitted for the caller's direct-transport fallback.

        A single storage axis uses twice the AOD minimum spacing. A single
        gate-pair column uses pair separation plus three minimum spacings.
        Explicit geometry can replace those otherwise unspecified pitches.
        """
        spacing = device.aod(aod_id).min_spacing
        result: dict[str, SiteStagingGeometry] = {}
        gate_sites = {
            site.id
            for zone in device.zones
            if "cz" in zone.operations
            for site in device.slm_sites
            if zone.bounds.contains(site.position)
        }
        ordered = sorted(device.zones, key=lambda zone: "cz" not in zone.operations)
        for zone in ordered:
            sites = tuple(
                site
                for site in device.slm_sites
                if site.id not in result
                and zone.bounds.contains(site.position)
                and ("cz" in zone.operations or site.id not in gate_sites)
            )
            if not sites:
                continue
            if "cz" in zone.operations:
                if device.cz is not None:
                    result.update(
                        _gate_arrays(sites, zone.id, device.cz.radius_um, spacing)
                    )
            else:
                result.update(_storage_array(sites, zone.id, spacing))
        return cls(result)


def _pitch(values: tuple[float, ...], fallback: float) -> float | None:
    if len(values) == 1:
        return fallback
    pitch = values[1] - values[0]
    if pitch <= COORDINATE_TOLERANCE or any(
        abs(right - left - pitch) > COORDINATE_TOLERANCE
        for left, right in pairwise(values)
    ):
        return None
    return pitch


def _grid_axes(
    sites: tuple[SLMSite, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    points = {site.position for site in sites}
    xs = tuple(sorted({point.x for point in points}))
    ys = tuple(sorted({point.y for point in points}))
    if len(points) != len(xs) * len(ys):
        return None
    return xs, ys


def _storage_array(
    sites: tuple[SLMSite, ...], zone_id: str, spacing: float
) -> dict[str, SiteStagingGeometry]:
    axes = _grid_axes(sites)
    if axes is None:
        return {}
    xs, ys = axes
    dx, dy = _pitch(xs, 2 * spacing), _pitch(ys, 2 * spacing)
    if dx is None or dy is None:
        return {}
    geometry = SiteStagingGeometry(
        "storage",
        dx,
        dy,
        xs[0],
        xs[0] + len(xs) * dx,
        ys[0],
        ys[0] + len(ys) * dy,
        0,
        zone_id,
    )
    return {site.id: geometry for site in sites}


def _gate_arrays(
    sites: tuple[SLMSite, ...], zone_id: str, radius: float, spacing: float
) -> dict[str, SiteStagingGeometry]:
    axes = _grid_axes(sites)
    if axes is None:
        return {}
    xs, ys = axes
    if len(xs) % 2:
        return {}
    left, right = xs[::2], xs[1::2]
    separation = right[0] - left[0]
    if (
        not 0 < separation <= radius
        or any(
            abs(b - a - separation) > COORDINATE_TOLERANCE
            for a, b in zip(left, right, strict=True)
        )
        or any(b - a <= radius for a, b in zip(right, left[1:], strict=False))
    ):
        return {}
    dx = _pitch(left, separation + 3 * spacing)
    dy = _pitch(ys, 2 * spacing)
    if dx is None or dy is None:
        return {}
    kind_origins: tuple[tuple[SiteKind, float], ...] = (
        ("left", left[0]),
        ("right", right[0]),
    )
    descriptors = {
        kind: SiteStagingGeometry(
            kind,
            dx,
            dy,
            origin,
            origin + len(left) * dx,
            ys[0],
            ys[0] + len(ys) * dy,
            separation,
            zone_id,
        )
        for kind, origin in kind_origins
    }
    left_positions = {Position(x, y) for x in left for y in ys}
    return {
        site.id: descriptors["left" if site.position in left_positions else "right"]
        for site in sites
    }
