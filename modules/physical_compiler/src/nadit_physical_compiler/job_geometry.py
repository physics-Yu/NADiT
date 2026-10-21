"""Decode explicit geometry options using Device site identities."""

from dataclasses import fields
from typing import Any, Literal, cast

from .coverage import RegionSet
from .device import Device, Position, Region, SLMSite
from .job_fields import array_at, keys_at, number_at, object_at, required, string_at
from .placement.geometry import PlacementGeometry, SLMArray
from .transport.staging_geometry import SiteStagingGeometry, StagingGeometry


def staging_geometry(value: Any, device: Device, path: str) -> StagingGeometry | None:
    """Decode transport geometry; None retains the constructor's inference."""
    if value is None:
        return None
    data = object_at(value, path)
    keys_at(data, {"sites", "coordinate_quantum"}, path)
    sites = object_at(required(data, "sites", path), f"{path}.sites")
    result: dict[str, SiteStagingGeometry] = {}
    names = {field.name for field in fields(SiteStagingGeometry)}
    for site_id, raw in sites.items():
        item_path = f"{path}.sites.{site_id}"
        device.site(site_id)
        item = object_at(raw, item_path)
        keys_at(item, names, item_path)
        values: dict[str, Any] = {}
        for name in names:
            field = required(item, name, item_path)
            values[name] = (
                string_at(field, f"{item_path}.{name}")
                if name in {"kind", "zone_id"}
                else number_at(field, f"{item_path}.{name}")
            )
        if values["kind"] not in ("storage", "left", "right"):
            raise ValueError(f"{item_path}.kind: expected storage, left, or right.")
        result[site_id] = SiteStagingGeometry(**values)
    quantum = data.get("coordinate_quantum")
    return StagingGeometry(
        result,
        None if quantum is None else number_at(quantum, f"{path}.coordinate_quantum"),
    )


def placement_geometry(
    value: Any, device: Device, path: str
) -> PlacementGeometry | None:
    """Decode ordered SLM arrays whose site matrices contain Device site IDs."""
    if value is None:
        return None
    data = object_at(value, path)
    keys_at(data, {"storage_arrays", "entanglement_arrays"}, path)
    storage = tuple(
        _array(item, device, f"{path}.storage_arrays[{i}]")
        for i, item in enumerate(
            array_at(required(data, "storage_arrays", path), f"{path}.storage_arrays")
        )
    )
    gates: list[tuple[SLMArray, SLMArray]] = []
    for index, item in enumerate(
        array_at(
            required(data, "entanglement_arrays", path), f"{path}.entanglement_arrays"
        )
    ):
        pair_path = f"{path}.entanglement_arrays[{index}]"
        pair = array_at(item, pair_path)
        if len(pair) != 2:
            raise ValueError(f"{pair_path}: expected two arrays.")
        gates.append(
            (
                _array(pair[0], device, f"{pair_path}[0]"),
                _array(pair[1], device, f"{pair_path}[1]"),
            )
        )
    return PlacementGeometry(storage, tuple(gates))


def _array(value: Any, device: Device, path: str) -> SLMArray:
    data = object_at(value, path)
    keys_at(data, {"id", "kind", "sites", "pitch", "partner_sites"}, path)
    kind = string_at(required(data, "kind", path), f"{path}.kind")
    if kind not in ("storage", "left", "right"):
        raise ValueError(f"{path}.kind: expected storage, left, or right.")
    pitch = object_at(required(data, "pitch", path), f"{path}.pitch")
    keys_at(pitch, {"x", "y"}, f"{path}.pitch")
    return SLMArray(
        string_at(required(data, "id", path), f"{path}.id"),
        cast(Literal["storage", "left", "right"], kind),
        _site_grid(required(data, "sites", path), device, f"{path}.sites"),
        Position(
            number_at(required(pitch, "x", f"{path}.pitch"), f"{path}.pitch.x"),
            number_at(required(pitch, "y", f"{path}.pitch"), f"{path}.pitch.y"),
        ),
        _site_grid(data.get("partner_sites", []), device, f"{path}.partner_sites"),
    )


def _site_grid(
    value: Any, device: Device, path: str
) -> tuple[tuple[SLMSite, ...], ...]:
    return tuple(
        tuple(
            device.site(string_at(site, f"{path}[{row}][{column}]"))
            for column, site in enumerate(array_at(items, f"{path}[{row}]"))
        )
        for row, items in enumerate(array_at(value, path))
    )


def region_coverage(value: Any, path: str) -> Region | RegionSet:
    """Decode explicit rectangular pulse coverage in the result codec's format."""
    data = object_at(value, path)
    kind = string_at(required(data, "kind", path), f"{path}.kind")
    if kind == "region":
        names = ("x_min", "y_min", "x_max", "y_max")
        keys_at(data, {"kind", "region"}, path)
        coordinates = object_at(required(data, "region", path), f"{path}.region")
        keys_at(coordinates, set(names), f"{path}.region")
        return Region(
            *(
                number_at(
                    required(coordinates, name, f"{path}.region"),
                    f"{path}.region.{name}",
                )
                for name in names
            )
        )
    if kind == "regions":
        keys_at(data, {"kind", "regions"}, path)
        regions: list[Region] = []
        for index, raw in enumerate(
            array_at(required(data, "regions", path), f"{path}.regions")
        ):
            region = region_coverage(
                {
                    "kind": "region",
                    "region": object_at(raw, f"{path}.regions[{index}]"),
                },
                f"{path}.regions[{index}]",
            )
            if not isinstance(region, Region):
                raise ValueError(f"{path}.regions[{index}]: expected a region.")
            regions.append(region)
        return RegionSet(tuple(regions))
    raise ValueError(f"{path}.kind: expected region or regions.")
