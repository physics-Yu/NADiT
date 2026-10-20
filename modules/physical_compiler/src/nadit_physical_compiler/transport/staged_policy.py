"""Recognize the regular-array geometry required by the source template."""

from collections import defaultdict
from math import isclose, isfinite

from ..device import AOD, Device, Position
from ..state import COORDINATE_TOLERANCE
from .staging_geometry import SiteStagingGeometry, StagingGeometry


def staging_regions(device: Device, aod: AOD, geometry: StagingGeometry) -> set[str]:
    """Return regions with the source template's regular fixed-site geometry.

    The AOD argument preserves the transport geometry interface. Actual AOD
    capacity, spacing, workspace, and occupied intersections are bound to
    constructed batch plans rather than whole-region envelopes.
    """
    grouped: dict[str, list[tuple[str, SiteStagingGeometry]]] = defaultdict(list)
    for site_id, description in geometry.sites.items():
        grouped[description.zone_id].append((site_id, description))
    positions = {site.id: site.position for site in device.slm_sites}
    admitted = set()
    for zone_id, entries in grouped.items():
        if _region_fits(positions, entries):
            admitted.add(zone_id)
    return admitted


def _region_fits(
    positions: dict[str, Position],
    entries: list[tuple[str, SiteStagingGeometry]],
) -> bool:
    descriptions = [description for _, description in entries]
    return _common_lattice(descriptions) and all(
        _on_lattice(positions[site_id], description) for site_id, description in entries
    )


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
    if not 0 < first.pair_separation < first.pitch_x:
        return False
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
