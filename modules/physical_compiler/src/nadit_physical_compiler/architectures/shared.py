"""Generate shared/v1 double-trap arrays with independent readout geometry."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from ..device import Position, SLMSite, Zone, loads_device
from ..job_fields import keys_at, required
from ..provenance import canonical_hash
from ..schemas.primitives import CZ, GLOBAL, LOCAL, TIMING
from ..serialization import encode_device
from .geometry import check_geometry
from .schema import parameter_schema as zoned_schema
from .zoned import _aod, _capability, _gates, _measurement


def description() -> dict[str, Any]:
    """Describe absolute array positions, local bounds, and existing control fields."""
    schema = deepcopy(zoned_schema())
    properties = schema["properties"]
    properties["shared_arrays"] = properties.pop("gate_zones")
    del properties["storage"]
    del properties["site_order"]
    schema["required"] = ["shared_arrays", "cz"]
    return {
        "parameters": schema,
        "defaults": {"timing": "linear"},
        "constraints": [
            "Absolute array/readout positions; bounds are local to each position.",
            "Each double-trap pair is isolated from all other pairs.",
            "All sites lie within their shared zone and AOD workspace.",
        ],
    }


def generate_shared(params: Mapping[str, Any]) -> dict[str, Any]:
    """Expand paired grids without a separate storage region.

    Uses the same grid/capability helpers as zoned/v1. Roles remain unspecified
    to reproduce legacy shared devices; operation permissions define the region.
    """
    data = dict(params)
    keys_at(data, set(description()["parameters"]["properties"]), "params")
    sites: list[SLMSite] = []
    zones: list[Zone] = []
    blocks = deepcopy(required(data, "shared_arrays", "params"))
    for block in blocks:
        block.setdefault("operations", ["cz", "rotate", "reset"])
    pairs = _gates(blocks, Position(0, 0), sites, zones)
    readout = _measurement(data.get("measurement"), Position(0, 0), zones)
    zones = [replace(zone, role=None) for zone in zones]
    timing = data.get("timing", "linear")
    if isinstance(timing, str):
        timing = {
            "load_us": 10,
            "store_us": 10,
            "move_us_per_um": 1,
            "move_profile": timing,
        }
    document = {
        "name": data.get("name", f"shared/v1-{canonical_hash(data)[:12]}"),
        "zones": [asdict(zone) for zone in zones],
        "slm_sites": [asdict(site) for site in sites],
        "pair_arrays": pairs,
        "aods": [_aod(data.get("aod", {}), zones)],
        "timing": _capability(timing, TIMING, "params.timing"),
        "cz": _capability(required(data, "cz", "params"), CZ, "params.cz"),
        "local": _capability(data.get("local"), LOCAL, "params.local"),
        "global_control": _capability(
            data.get("global_control"), GLOBAL, "params.global_control"
        ),
        "readout": readout,
    }
    device = loads_device(json.dumps(document))
    check_geometry(device, data.get("shared_zones", []))
    return encode_device(device)
