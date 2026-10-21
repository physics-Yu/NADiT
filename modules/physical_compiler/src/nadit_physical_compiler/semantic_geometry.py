"""Tolerant coverage geometry for independent semantic validation."""

from .coverage import RegionSet
from .device import Position, Region
from .state import COORDINATE_TOLERANCE


def contains(coverage: Region | RegionSet, point: Position) -> bool:
    """Return whether a point lies in the closed coverage within tolerance."""
    regions = (coverage,) if isinstance(coverage, Region) else coverage.regions
    tolerance = COORDINATE_TOLERANCE
    return any(
        region.x_min - tolerance <= point.x <= region.x_max + tolerance
        and region.y_min - tolerance <= point.y <= region.y_max + tolerance
        for region in regions
    )


def intersects_segment(region: Region, start: Position, end: Position) -> bool:
    """Return whether a straight segment intersects a closed rectangle.

    The slab intervals share a segment parameter in [0, 1]. Degenerate
    rectangles represent point addressing; no optical beam width is inferred.
    """
    lower, upper = 0.0, 1.0
    tolerance = COORDINATE_TOLERANCE
    for origin, target, minimum, maximum in (
        (start.x, end.x, region.x_min, region.x_max),
        (start.y, end.y, region.y_min, region.y_max),
    ):
        minimum -= tolerance
        maximum += tolerance
        delta = target - origin
        if delta == 0:
            if not minimum <= origin <= maximum:
                return False
            continue
        first, last = sorted(((minimum - origin) / delta, (maximum - origin) / delta))
        lower, upper = max(lower, first), min(upper, last)
        if lower > upper:
            return False
    return True
