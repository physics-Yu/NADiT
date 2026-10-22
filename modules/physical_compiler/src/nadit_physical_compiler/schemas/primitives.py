"""Small JSON Schema constructors shared by device, job, and architecture schemas."""

from typing import Any


def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """Describe a closed object with explicitly required properties."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def array(items: dict[str, Any], **constraints: Any) -> dict[str, Any]:
    """Describe a homogeneous array with optional length constraints."""
    return {"type": "array", "items": items, **constraints}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Permit null in addition to the given schema."""
    return {"anyOf": [schema, {"type": "null"}]}


def fixed(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe an exact-length heterogeneous tuple."""
    return {
        "type": "array",
        "prefixItems": items,
        "items": False,
        "minItems": len(items),
        "maxItems": len(items),
    }


STRING: dict[str, Any] = {"type": "string"}
NUMBER: dict[str, Any] = {"type": "number"}
INTEGER: dict[str, Any] = {"type": "integer"}
BOOLEAN: dict[str, Any] = {"type": "boolean"}
POSITIVE: dict[str, Any] = {"type": "number", "exclusiveMinimum": 0}
COUNT: dict[str, Any] = {"type": "integer", "minimum": 1}
NONNEGATIVE: dict[str, Any] = {"type": "number", "minimum": 0}
POSITION = obj({"x": NUMBER, "y": NUMBER}, ("x", "y"))
PITCH = obj({"x": POSITIVE, "y": POSITIVE}, ("x", "y"))
BOUNDS = obj(
    {name: NUMBER for name in ("x_min", "y_min", "x_max", "y_max")},
    ("x_min", "y_min", "x_max", "y_max"),
)
ROLE: dict[str, Any] = {"enum": ["storage", "entanglement", "measurement"]}
HOLDER: dict[str, Any] = {"enum": ["slm", "aod"]}
MATRIX = array(array(STRING, minItems=1), minItems=1)
GRID = obj(
    {
        "prefix": STRING,
        "origin": POSITION,
        "rows": COUNT,
        "columns": COUNT,
        "pitch": PITCH,
    },
    ("prefix", "origin", "rows", "columns", "pitch"),
)
TIMING = obj(
    {
        "load_us": NONNEGATIVE,
        "store_us": NONNEGATIVE,
        "move_us_per_um": NONNEGATIVE,
        "move_profile": {"enum": ["linear", "paper"]},
    },
    ("load_us", "store_us", "move_us_per_um"),
)
CZ = obj(
    {
        "channel": STRING,
        "radius_um": POSITIVE,
        "duration_us": NONNEGATIVE,
        "holder_pairs": array(fixed([HOLDER, HOLDER])),
        "allow_spectators": BOOLEAN,
    },
    ("channel", "radius_um", "duration_us"),
)
LOCAL = obj(
    {
        "channel": STRING,
        "rotation_us": NONNEGATIVE,
        "reset_us": NONNEGATIVE,
        "holders": array(HOLDER),
    },
    ("channel", "rotation_us", "reset_us"),
)
GLOBAL = obj(
    {
        "channel": STRING,
        "rotation_us": NONNEGATIVE,
        "holders": array(HOLDER),
        "region": nullable(fixed([NUMBER] * 4)),
    },
    ("channel", "rotation_us"),
)
READOUT = obj(
    {
        "channel": STRING,
        "duration_us": NONNEGATIVE,
        "latency_us": NONNEGATIVE,
        "min_spacing_um": NONNEGATIVE,
        "max_atoms": nullable(COUNT),
        "retain_atoms": BOOLEAN,
        "holders": array(HOLDER),
    },
    ("channel", "duration_us"),
)
AOD_PROPERTIES = {
    "id": STRING,
    "workspace": BOUNDS,
    "min_spacing": POSITIVE,
    "max_rows": nullable(COUNT),
    "max_columns": nullable(COUNT),
    "motion": {"enum": ["flexible", "rigid"]},
}
