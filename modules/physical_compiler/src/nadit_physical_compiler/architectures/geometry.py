"""Geometry helpers for deterministic zoned Device expansion."""

from itertools import combinations
from typing import Any

from ..device import Device, Position, Region, SLMSite, slm_grid
from ..job_fields import (
    array_at,
    integer_at,
    keys_at,
    number_at,
    object_at,
    required,
    string_at,
)


def point(value: Any, path: str) -> Position:
    """Read a finite x/y coordinate with a parameter-path diagnostic."""
    raw = object_at(value, path)
    keys_at(raw, {"x", "y"}, path)
    return Position(
        *(number_at(required(raw, key, path), f"{path}.{key}") for key in ("x", "y"))
    )


def count(value: Any, path: str) -> int:
    """Read a positive lattice dimension."""
    result = integer_at(value, path)
    if result <= 0:
        raise ValueError(f"{path}: must be positive.")
    return result


def bounds(value: Any, origin: Position, path: str) -> Region:
    """Translate local rectangular bounds by a block origin."""
    raw = object_at(value, path)
    names = ("x_min", "y_min", "x_max", "y_max")
    keys_at(raw, set(names), path)
    values = [number_at(required(raw, key, path), f"{path}.{key}") for key in names]
    try:
        return Region(
            *(
                value + offset
                for value, offset in zip(
                    values, (origin.x, origin.y, origin.x, origin.y), strict=True
                )
            )
        )
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def grid(
    prefix: str,
    origin: Position,
    rows: int,
    columns: int,
    pitch: Position,
    ids: Any,
    path: str,
) -> tuple[tuple[SLMSite, ...], tuple[tuple[str, ...], ...]]:
    """Expand a regular grid, optionally retaining legacy site IDs."""
    if pitch.x <= 0 or pitch.y <= 0:
        raise ValueError(f"{path}.pitch: both components must be positive.")
    sites = slm_grid(prefix, origin, rows, columns, pitch)
    matrix = tuple(
        tuple(site.id for site in sites[start : start + columns])
        for start in range(0, len(sites), columns)
    )
    if ids is not None:
        matrix = tuple(
            tuple(
                string_at(site, f"{path}.ids[{r}][{c}]")
                for c, site in enumerate(array_at(row, f"{path}.ids[{r}]"))
            )
            for r, row in enumerate(array_at(ids, f"{path}.ids"))
        )
        if len(matrix) != rows or any(len(row) != columns for row in matrix):
            raise ValueError(f"{path}.ids: must match the generated grid dimensions.")
        sites = tuple(
            SLMSite(name, site.position)
            for name, site in zip(
                (name for row in matrix for name in row), sites, strict=True
            )
        )
    return sites, matrix


def check_geometry(device: Device, shared: Any) -> None:
    """Check declared overlap and workspace coverage once after expansion."""
    names = {zone.id for zone in device.zones}
    allowed = set()
    for index, raw in enumerate(array_at(shared, "params.shared_zones")):
        pair = array_at(raw, f"params.shared_zones[{index}]")
        if len(pair) != 2 or any(
            not isinstance(name, str) or name not in names for name in pair
        ):
            raise ValueError(
                f"params.shared_zones[{index}]: expected two known zone IDs."
            )
        allowed.add(frozenset(pair))
    for first, second in combinations(device.zones, 2):
        a, b = first.bounds, second.bounds
        overlaps = max(a.x_min, b.x_min) <= min(a.x_max, b.x_max) and max(
            a.y_min, b.y_min
        ) <= min(a.y_max, b.y_max)
        if overlaps and frozenset((first.id, second.id)) not in allowed:
            raise ValueError(
                f"params.shared_zones: undeclared overlap {first.id!r}/{second.id!r}."
            )
    for site in device.slm_sites:
        if not device.aods[0].workspace.contains(site.position):
            raise ValueError(f"params.aod.workspace: does not cover site {site.id!r}.")
