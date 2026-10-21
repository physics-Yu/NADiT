"""Expand zoned/v1 parameters into a self-contained Device document."""

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Literal

from ..device import Position, SLMSite, Zone, loads_device
from ..job_fields import (
    array_at,
    boolean_at,
    keys_at,
    number_at,
    object_at,
    required,
    string_at,
)
from ..provenance import canonical_hash
from ..schemas.primitives import CZ, GLOBAL, LOCAL, READOUT, TIMING
from ..serialization import encode_device
from .geometry import bounds, check_geometry, count, grid, point
from .schema import DEFAULTS, parameter_schema

STORAGE_OPERATIONS = ["load", "store", "rotate", "reset"]
GATE_OPERATIONS = ["load", "store", "cz", "rotate", "reset"]


def generate_zoned(params: Mapping[str, Any]) -> dict[str, Any]:
    """Generate zoned/v1 with stable site order, explicit capabilities, and one AOD.

    Args:
        params: Parameters described by architectures.schema.parameter_schema.
    Returns:
        An expanded Device document with role and pair-array metadata.
    Raises:
        ValueError: Invalid parameters or geometry, with the relevant input path.
    """
    data = dict(params)
    keys_at(data, set(parameter_schema()["properties"]), "params")
    blocks = array_at(required(data, "storage", "params"), "params.storage")
    if not blocks:
        raise ValueError("params.storage: at least one storage block is required.")
    sites: list[SLMSite] = []
    zones: list[Zone] = []
    reference = _storage(blocks, sites, zones)
    pairs = _gates(required(data, "gate_zones", "params"), reference, sites, zones)
    readout = _measurement(data.get("measurement"), reference, zones)
    aod = _aod(data.get("aod", {}), zones)
    timing = data.get("timing", DEFAULTS["timing"])
    if isinstance(timing, str):
        if timing not in ("linear", "paper"):
            raise ValueError("params.timing: expected linear, paper, or Timing fields.")
        timing = {
            "load_us": 10,
            "store_us": 10,
            "move_us_per_um": 1,
            "move_profile": timing,
        }
    timing = _capability(timing, TIMING, "params.timing")
    site_order = data.get("site_order")
    if site_order is not None:
        order = [
            string_at(name, f"params.site_order[{i}]")
            for i, name in enumerate(array_at(site_order, "params.site_order"))
        ]
        if len(order) != len(sites) or set(order) != {site.id for site in sites}:
            raise ValueError(
                "params.site_order: list every generated site exactly once."
            )
        by_id = {site.id: site for site in sites}
        sites = [by_id[name] for name in order]
    document = {
        "name": string_at(
            data.get("name", f"zoned/v1-{canonical_hash(data)[:12]}"), "params.name"
        ),
        "zones": [asdict(zone) for zone in zones],
        "slm_sites": [asdict(site) for site in sites],
        "pair_arrays": pairs,
        "aods": [aod],
        "timing": timing,
        "cz": _capability(required(data, "cz", "params"), CZ, "params.cz"),
        "local": _capability(data.get("local"), LOCAL, "params.local"),
        "global_control": _capability(
            data.get("global_control"), GLOBAL, "params.global_control"
        ),
        "readout": readout,
    }
    try:
        device = loads_device(json.dumps(document))
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"params: {error}") from error
    check_geometry(device, data.get("shared_zones", []))
    return encode_device(device)


def _block(raw: Any, path: str, group: str) -> dict[str, Any]:
    data = object_at(raw, path)
    schema = parameter_schema()["properties"][group]["items"]
    keys_at(data, set(schema["properties"]), path)
    for name in schema["required"]:
        required(data, name, path)
    return data


def _zone(
    data: dict[str, Any],
    origin: Position,
    extent: tuple[float, float],
    role: Literal["storage", "entanglement", "measurement"],
    path: str,
) -> Zone:
    rectangle = data.get(
        "bounds", {"x_min": 0, "y_min": 0, "x_max": extent[0], "y_max": extent[1]}
    )
    operations = data.get(
        "operations",
        GATE_OPERATIONS
        if role == "entanglement"
        else ["measure"]
        if role == "measurement"
        else STORAGE_OPERATIONS,
    )
    # The role is assigned by the family group, not inferred from the zone ID.
    return Zone(
        string_at(data["id"], f"{path}.id"),
        bounds(rectangle, origin, f"{path}.bounds"),
        tuple(
            string_at(value, f"{path}.operations[{i}]")
            for i, value in enumerate(array_at(operations, f"{path}.operations"))
        ),
        role,
    )


def _storage(blocks: list[Any], sites: list[SLMSite], zones: list[Zone]) -> Position:
    origins = []
    for index, raw in enumerate(blocks):
        path = f"params.storage[{index}]"
        block = _block(raw, path, "storage")
        origin = point(block["origin"], f"{path}.origin")
        pitch = point(block["pitch"], f"{path}.pitch")
        rows, columns = (
            count(block["rows"], f"{path}.rows"),
            count(block["columns"], f"{path}.columns"),
        )
        prefix = string_at(block.get("prefix", block["id"]), f"{path}.prefix")
        generated, _ = grid(prefix, origin, rows, columns, pitch, None, path)
        zone = _zone(
            block, origin, (columns * pitch.x, rows * pitch.y), "storage", path
        )
        if any(not zone.bounds.contains(site.position) for site in generated):
            raise ValueError(f"{path}.bounds: must contain all storage sites.")
        sites.extend(generated)
        zones.append(zone)
        origins.append(origin)
    return origins[0]


def _gates(
    value: Any, reference: Position, sites: list[SLMSite], zones: list[Zone]
) -> list[dict[str, Any]]:
    raw_gates = array_at(value, "params.gate_zones")
    if not raw_gates:
        raise ValueError("params.gate_zones: at least one paired array is required.")
    result = []
    for index, raw in enumerate(raw_gates):
        path = f"params.gate_zones[{index}]"
        block = _block(raw, path, "gate_zones")
        offset = point(block["position"], f"{path}.position")
        origin = Position(reference.x + offset.x, reference.y + offset.y)
        pitch = point(block["pitch"], f"{path}.pitch")
        rows = count(block["rows"], f"{path}.rows")
        columns = count(block["pairs_per_row"], f"{path}.pairs_per_row")
        separation = number_at(block["separation"], f"{path}.separation")
        if separation <= 0:
            raise ValueError(f"{path}.separation: must be positive.")
        sides: dict[str, Any] = {}
        for side, shift in (("left", 0), ("right", separation)):
            prefix = string_at(
                block.get(f"{side}_prefix", f"{block['id']}_{side}"),
                f"{path}.{side}_prefix",
            )
            generated, matrix = grid(
                prefix,
                Position(origin.x + shift, origin.y),
                rows,
                columns,
                pitch,
                block.get(f"{side}_ids"),
                f"{path}.{side}",
            )
            sites.extend(generated)
            sides[side] = matrix
        zones.append(
            _zone(
                block,
                origin,
                ((columns - 1) * pitch.x + separation, (rows - 1) * pitch.y),
                "entanglement",
                path,
            )
        )
        result.append(
            {
                "id": string_at(block.get("array_id", block["id"]), f"{path}.array_id"),
                "zone": block["id"],
                **sides,
            }
        )
    return result


def _measurement(
    value: Any, reference: Position, zones: list[Zone]
) -> dict[str, Any] | None:
    if value is None:
        return None
    path = "params.measurement"
    data = object_at(value, path)
    schema = parameter_schema()["properties"]["measurement"]
    keys_at(data, set(schema["properties"]), path)
    offset = point(required(data, "position", path), f"{path}.position")
    required(data, "bounds", path)
    zones.append(
        _zone(
            {"id": "measurement", **data},
            Position(reference.x + offset.x, reference.y + offset.y),
            (0, 0),
            "measurement",
            path,
        )
    )
    return _capability(required(data, "readout", path), READOUT, f"{path}.readout")


def _aod(value: Any, zones: list[Zone]) -> dict[str, Any]:
    raw = object_at(value, "params.aod")
    keys_at(
        raw, set(parameter_schema()["properties"]["aod"]["properties"]), "params.aod"
    )
    data = {**DEFAULTS["aod"], **raw}
    margin = number_at(data.pop("margin"), "params.aod.margin")
    if margin < 0:
        raise ValueError("params.aod.margin: must be nonnegative.")
    for key in ("max_rows", "max_columns"):
        if data[key] is not None:
            data[key] = count(data[key], f"params.aod.{key}")
    data["min_spacing"] = number_at(data["min_spacing"], "params.aod.min_spacing")
    if data["min_spacing"] <= 0:
        raise ValueError("params.aod.min_spacing: must be positive.")
    data["id"] = string_at(data["id"], "params.aod.id")
    if data["motion"] not in ("flexible", "rigid"):
        raise ValueError("params.aod.motion: expected flexible or rigid.")
    if "workspace" not in data:
        data["workspace"] = {
            "x_min": min(zone.bounds.x_min for zone in zones) - margin,
            "y_min": min(zone.bounds.y_min for zone in zones) - margin,
            "x_max": max(zone.bounds.x_max for zone in zones) + margin,
            "y_max": max(zone.bounds.y_max for zone in zones) + margin,
        }
    else:
        data["workspace"] = asdict(
            bounds(data["workspace"], Position(0, 0), "params.aod.workspace")
        )
    return data


def _capability(value: Any, schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    if value is None:
        return None
    data = object_at(value, path)
    keys_at(data, set(schema["properties"]), path)
    for name in schema["required"]:
        required(data, name, path)
    for name, raw in data.items():
        descriptor = schema["properties"][name]
        if descriptor.get("type") == "number":
            number = number_at(raw, f"{path}.{name}")
            if number < 0 or ("exclusiveMinimum" in descriptor and number == 0):
                raise ValueError(
                    f"{path}.{name}: outside its positive/nonnegative range."
                )
        elif descriptor.get("type") == "string":
            string_at(raw, f"{path}.{name}")
        elif descriptor.get("type") == "boolean":
            boolean_at(raw, f"{path}.{name}")
        elif name in ("holders", "holder_pairs"):
            items = array_at(raw, f"{path}.{name}")
            if name == "holder_pairs":
                pairs = [
                    array_at(pair, f"{path}.{name}[{i}]")
                    for i, pair in enumerate(items)
                ]
                if any(len(pair) != 2 for pair in pairs):
                    raise ValueError(f"{path}.{name}: expected holder pairs.")
                items = [holder for pair in pairs for holder in pair]
            if any(holder not in ("slm", "aod") for holder in items):
                raise ValueError(f"{path}.{name}: expected slm or aod holders.")
        elif name == "region" and raw is not None:
            region = array_at(raw, f"{path}.region")
            if len(region) != 4:
                raise ValueError(f"{path}.region: expected four coordinates.")
            for index, coordinate in enumerate(region):
                number_at(coordinate, f"{path}.region[{index}]")
    return data
