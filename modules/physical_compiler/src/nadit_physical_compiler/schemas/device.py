"""Schema for expanded or grid-based Device documents."""

from typing import Any

from .primitives import (
    AOD_PROPERTIES,
    BOUNDS,
    CZ,
    GLOBAL,
    GRID,
    LOCAL,
    MATRIX,
    POSITION,
    READOUT,
    ROLE,
    STRING,
    TIMING,
    array,
    nullable,
    obj,
)


def device_schema() -> dict[str, Any]:
    """Return Device JSON including optional roles and pair-array declarations."""
    side = {"oneOf": [GRID, MATRIX]}
    return obj(
        {
            "name": STRING,
            "zones": array(
                obj(
                    {
                        "id": STRING,
                        "bounds": BOUNDS,
                        "operations": array(STRING),
                        "role": nullable(ROLE),
                    },
                    ("id", "bounds", "operations"),
                )
            ),
            "slm_sites": array(
                obj({"id": STRING, "position": POSITION}, ("id", "position"))
            ),
            "slm_grids": array(GRID),
            "pair_arrays": array(
                obj(
                    {"id": STRING, "zone": STRING, "left": side, "right": side},
                    ("id", "zone", "left", "right"),
                )
            ),
            "aods": array(obj(AOD_PROPERTIES, ("id", "workspace", "min_spacing"))),
            "timing": TIMING,
            "cz": nullable(CZ),
            "local": nullable(LOCAL),
            "readout": nullable(READOUT),
            "global_control": nullable(GLOBAL),
        },
        ("name", "zones", "aods", "timing"),
    )
