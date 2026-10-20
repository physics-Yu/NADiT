"""Admit regular staging regions with space for the reference offset lanes."""

from collections import defaultdict
from itertools import pairwise
from math import isclose, isfinite

from ..device import AOD, Device, Position, Region
from ..state import COORDINATE_TOLERANCE
from .staging_geometry import SiteStagingGeometry, StagingGeometry


def staging_regions(device: Device, aod: AOD, geometry: StagingGeometry) -> set[str]:
    """Return regions with regular fixed sites and usable offset corridors.

    Admission establishes spacing, workspace margins, and absence of foreign
    fixed traps in the staging region once, before selecting movement groups.
    Missing or unsuitable regions use the direct transporter.
    """
    grouped: dict[str, list[tuple[str, SiteStagingGeometry]]] = defaultdict(list)
    for site_id, description in geometry.sites.items():
        grouped[description.zone_id].append((site_id, description))
    positions = {site.id: site.position for site in device.slm_sites}
    admitted = set()
    for zone_id, entries in grouped.items():
        if _region_fits(positions, aod, entries):
            admitted.add(zone_id)
    return admitted


def _region_fits(
    positions: dict[str, Position],
    aod: AOD,
    entries: list[tuple[str, SiteStagingGeometry]],
) -> bool:
    descriptions = [description for _, description in entries]
    if not _common_lattice(descriptions) or not all(
        _on_lattice(positions[site_id], description) for site_id, description in entries
    ):
        return False
    pitch_x = descriptions[0].pitch_x
    pitch_y = descriptions[0].pitch_y
    # The reference's strict shifts remain in these lattice lanes and margins.
    bounds = Region(
        min(item.min_x for item in descriptions) - pitch_x,
        min(item.min_y for item in descriptions) - pitch_y / 2,
        max(item.max_x for item in descriptions),
        max(item.max_y for item in descriptions),
    )
    workspace = aod.workspace
    tolerance = COORDINATE_TOLERANCE
    if not (
        workspace.x_min - tolerance <= bounds.x_min
        and workspace.y_min - tolerance <= bounds.y_min
        and bounds.x_max <= workspace.x_max + tolerance
        and bounds.y_max <= workspace.y_max + tolerance
    ):
        return False
    allowed_sites = {site_id for site_id, _ in entries}
    if any(
        bounds.contains(position) and site_id not in allowed_sites
        for site_id, position in positions.items()
    ):
        return False
    ys = sorted({positions[site_id].y for site_id, _ in entries})
    if pitch_y / 2 < aod.min_spacing - tolerance:
        return False
    for sign in (-1, 1):
        if not _lanes_fit(
            {value: value + sign * pitch_y / 2 for value in ys}, aod.min_spacing
        ):
            return False
    for columnwise in (False, True):
        lanes = {
            positions[site_id].x: positions[site_id].x + _offset(item, columnwise)
            for site_id, item in entries
        }
        if not _lanes_fit(lanes, aod.min_spacing):
            return False
    return True


def _common_lattice(descriptions: list[SiteStagingGeometry]) -> bool:
    """Admit the generator's one regular array or paired-array shape per region."""
    first = descriptions[0]
    fields = ("pitch_x", "pitch_y", "min_y", "max_y", "pair_separation")
    if first.pitch_x <= 0 or first.pitch_y <= 0:
        return False
    if any(
        not _same(getattr(item, field), getattr(first, field))
        for item in descriptions
        for field in fields
    ):
        return False
    by_kind: dict[str, SiteStagingGeometry] = {}
    for item in descriptions:
        previous = by_kind.setdefault(item.kind, item)
        if not (
            _same(item.min_x, previous.min_x) and _same(item.max_x, previous.max_x)
        ):
            return False
    if "storage" in by_kind:
        return len(by_kind) == 1
    if "left" in by_kind and "right" in by_kind:
        left, right = by_kind["left"], by_kind["right"]
        return _same(right.min_x - left.min_x, first.pair_separation) and _same(
            right.max_x - left.max_x, first.pair_separation
        )
    return True


def _on_lattice(position: Position, item: SiteStagingGeometry) -> bool:
    for coordinate, origin, end, pitch in (
        (position.x, item.min_x, item.max_x, item.pitch_x),
        (position.y, item.min_y, item.max_y, item.pitch_y),
    ):
        if not all(isfinite(value) for value in (origin, end, pitch)):
            return False
        if not origin - COORDINATE_TOLERANCE <= coordinate < end:
            return False
        if not _same(coordinate, origin + round((coordinate - origin) / pitch) * pitch):
            return False
    return True


def _same(first: float, second: float) -> bool:
    return isclose(first, second, rel_tol=0, abs_tol=COORDINATE_TOLERANCE)


def _offset(item: SiteStagingGeometry, columnwise: bool) -> float:
    if item.kind == "storage":
        return item.pitch_x / 2
    value = (
        (item.pitch_x - item.pair_separation) / 3 if columnwise else item.pitch_x / 4
    )
    return -value if item.kind == "left" else value


def _lanes_fit(lanes: dict[float, float], spacing: float) -> bool:
    intervals = sorted(
        (min(original, parked), max(original, parked))
        for original, parked in lanes.items()
    )
    tolerance = COORDINATE_TOLERANCE
    return all(
        abs(parked - original) >= spacing - tolerance
        for original, parked in lanes.items()
    ) and all(
        right[0] - left[1] >= spacing - tolerance for left, right in pairwise(intervals)
    )
