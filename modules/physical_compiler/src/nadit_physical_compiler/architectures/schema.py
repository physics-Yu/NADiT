"""Parameters and defaults of the versioned zoned architecture family."""

from copy import deepcopy
from typing import Any

from ..schemas.primitives import (
    AOD_PROPERTIES,
    BOUNDS,
    COUNT,
    CZ,
    GLOBAL,
    LOCAL,
    MATRIX,
    NONNEGATIVE,
    PITCH,
    POSITION,
    POSITIVE,
    READOUT,
    STRING,
    TIMING,
    array,
    fixed,
    nullable,
    obj,
)

DEFAULTS: dict[str, Any] = {
    "timing": "linear",
    "aod": {
        "id": "transport",
        "motion": "flexible",
        "min_spacing": 1,
        "max_rows": None,
        "max_columns": None,
        "margin": 10,
    },
    "shared_zones": [],
}


def parameter_schema() -> dict[str, Any]:
    """Describe zoned/v1 parameters; coordinates and durations use um and us."""
    storage = obj(
        {
            "id": STRING,
            "prefix": STRING,
            "origin": POSITION,
            "rows": COUNT,
            "columns": COUNT,
            "pitch": PITCH,
            "bounds": BOUNDS,
            "operations": array(STRING),
        },
        ("id", "origin", "rows", "columns", "pitch"),
    )
    gates = obj(
        {
            "id": STRING,
            "array_id": STRING,
            "position": POSITION,
            "rows": COUNT,
            "pairs_per_row": COUNT,
            "separation": POSITIVE,
            "pitch": PITCH,
            "bounds": BOUNDS,
            "operations": array(STRING),
            "left_prefix": STRING,
            "right_prefix": STRING,
            "left_ids": MATRIX,
            "right_ids": MATRIX,
        },
        ("id", "position", "rows", "pairs_per_row", "separation", "pitch"),
    )
    return obj(
        {
            "name": STRING,
            "storage": array(storage, minItems=1),
            "gate_zones": array(gates, minItems=1),
            "measurement": obj(
                {
                    "id": STRING,
                    "position": POSITION,
                    "bounds": BOUNDS,
                    "operations": array(STRING),
                    "readout": READOUT,
                },
                ("position", "bounds", "readout"),
            ),
            "aod": obj({**AOD_PROPERTIES, "margin": NONNEGATIVE}),
            "timing": {"oneOf": [{"enum": ["linear", "paper"]}, TIMING]},
            "cz": CZ,
            "local": nullable(LOCAL),
            "global_control": nullable(GLOBAL),
            "shared_zones": array(fixed([STRING, STRING])),
            "site_order": array(STRING, uniqueItems=True),
        },
        ("storage", "gate_zones", "cz"),
    )


def description() -> dict[str, Any]:
    """Return independently owned schema, defaults, and geometric constraints."""
    return {
        "parameters": parameter_schema(),
        "defaults": deepcopy(DEFAULTS),
        "constraints": [
            "Closed zone bounds may overlap only in declared shared_zones pairs.",
            "The AOD workspace must contain every generated site.",
            "Pair distance is positive and at most CZ radius; "
            "other pairs do not interact.",
            "Storage capacity is the number of distinct storage sites.",
            "Gate/measurement positions are offsets from the first storage origin.",
            "Bounds are local to each block origin or translated position.",
        ],
    }
