"""Decode pair grids and check device interaction geometry once at admission."""

from itertools import combinations
from math import hypot
from typing import Any

from .device import Device, PairArray, Position, SLMSite, slm_grid
from .job_fields import array_at, integer_at, keys_at, object_at, required, string_at


def decode_pair_arrays(value: Any) -> tuple[tuple[PairArray, ...], tuple[SLMSite, ...]]:
    """Read grid descriptors or site-ID matrices, returning new sites separately.

    Raises:
        ValueError: A matrix, descriptor, or field is invalid, with its input path.
    """
    arrays: list[PairArray] = []
    sites: list[SLMSite] = []
    for index, raw in enumerate(array_at(value, "pair_arrays")):
        path = f"pair_arrays[{index}]"
        item = object_at(raw, path)
        keys_at(item, {"id", "zone", "left", "right"}, path)
        sides = []
        for side in ("left", "right"):
            raw_side = required(item, side, path)
            side_path = f"{path}.{side}"
            if isinstance(raw_side, dict):
                keys_at(
                    raw_side,
                    {"prefix", "origin", "rows", "columns", "pitch"},
                    side_path,
                )
                try:
                    generated = slm_grid(
                        string_at(raw_side["prefix"], f"{side_path}.prefix"),
                        Position(**raw_side["origin"]),
                        integer_at(raw_side["rows"], f"{side_path}.rows"),
                        integer_at(raw_side["columns"], f"{side_path}.columns"),
                        Position(**raw_side["pitch"]),
                    )
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"{side_path}: {error}") from error
                sites.extend(generated)
                width = raw_side["columns"]
                sides.append(
                    tuple(
                        tuple(site.id for site in generated[start : start + width])
                        for start in range(0, len(generated), width)
                    )
                )
            else:
                sides.append(
                    tuple(
                        tuple(
                            string_at(site, f"{side_path}[{r}][{c}]")
                            for c, site in enumerate(array_at(row, f"{side_path}[{r}]"))
                        )
                        for r, row in enumerate(array_at(raw_side, side_path))
                    )
                )
        arrays.append(
            PairArray(
                string_at(required(item, "id", path), f"{path}.id"),
                string_at(required(item, "zone", path), f"{path}.zone"),
                sides[0],
                sides[1],
            )
        )
    return tuple(arrays), tuple(sites)


def validate_pair_arrays(device: Device) -> None:
    """Check shapes, ownership, and all within/across-array interaction distances.

    Raises:
        ValueError: Pair metadata cannot describe isolated CZ sites in this Device.
    """
    if device.cz is None:
        raise ValueError("pair_arrays: a CZ capability is required.")
    resolved: list[tuple[SLMSite, SLMSite]] = []
    zones = {zone.id: zone for zone in device.zones}
    used: set[str] = set()
    for index, array in enumerate(device.pair_arrays):
        path = f"pair_arrays[{index}]"
        shape = tuple(len(row) for row in array.left)
        if (
            not shape
            or not shape[0]
            or len(set(shape)) != 1
            or shape != tuple(len(row) for row in array.right)
        ):
            raise ValueError(
                f"{path}: left/right must have equal nonempty rectangular shapes."
            )
        zone = zones.get(array.zone)
        if zone is None or "cz" not in zone.operations:
            raise ValueError(f"{path}.zone: expected a zone permitting CZ.")
        for a, b in array.pairs:
            try:
                pair = device.site(a), device.site(b)
            except KeyError as error:
                raise ValueError(f"{path}: unknown site {error}.") from error
            for site in pair:
                if site.id in used:
                    raise ValueError(f"{path}: repeated site {site.id!r}.")
                used.add(site.id)
                if not zone.bounds.contains(site.position):
                    raise ValueError(f"{path}: site {site.id!r} is outside its zone.")
            if not 0 < _distance(*pair) <= device.cz.radius_um:
                raise ValueError(
                    f"{path}: pair distance must be positive and within CZ radius."
                )
            resolved.append(pair)
    if any(
        _distance(a, b) <= device.cz.radius_um
        for first, second in combinations(resolved, 2)
        for a in first
        for b in second
    ):
        raise ValueError("pair_arrays: sites from different pairs must not interact.")


def _distance(a: SLMSite, b: SLMSite) -> float:
    return hypot(a.position.x - b.position.x, a.position.y - b.position.y)
