"""Job JSON Schema built from registered stages and architecture descriptions."""

from copy import deepcopy
from typing import Any

from ..architectures import describe_families
from ..registry import REGISTRY
from .device import device_schema
from .primitives import (
    BOUNDS,
    INTEGER,
    MATRIX,
    NUMBER,
    PITCH,
    STRING,
    array,
    fixed,
    nullable,
    obj,
)


def _coverage() -> dict[str, Any]:
    return {
        "oneOf": [
            obj({"kind": {"const": "region"}, "region": BOUNDS}, ("kind", "region")),
            obj(
                {"kind": {"const": "regions"}, "regions": array(BOUNDS, minItems=1)},
                ("kind", "regions"),
            ),
        ]
    }


def _geometry(stage: str) -> dict[str, Any]:
    if stage == "transport":
        site = obj(
            {
                "kind": {"enum": ["storage", "left", "right"]},
                "zone_id": STRING,
                **{
                    name: NUMBER
                    for name in (
                        "pitch_x",
                        "pitch_y",
                        "min_x",
                        "max_x",
                        "min_y",
                        "max_y",
                        "pair_separation",
                    )
                },
            },
            (
                "kind",
                "zone_id",
                "pitch_x",
                "pitch_y",
                "min_x",
                "max_x",
                "min_y",
                "max_y",
                "pair_separation",
            ),
        )
        return obj(
            {
                "sites": {"type": "object", "additionalProperties": site},
                "coordinate_quantum": nullable(
                    {"type": "number", "exclusiveMinimum": 0}
                ),
            },
            ("sites",),
        )
    slm = obj(
        {
            "id": STRING,
            "kind": {"enum": ["storage", "left", "right"]},
            "sites": MATRIX,
            "pitch": PITCH,
            "partner_sites": array(array(STRING)),
        },
        ("id", "kind", "sites", "pitch"),
    )
    return obj(
        {"storage_arrays": array(slm), "entanglement_arrays": array(fixed([slm, slm]))},
        ("storage_arrays", "entanglement_arrays"),
    )


def stage_schema(stage: str, kind: str) -> dict[str, Any]:
    """Describe the exact option keys/defaults for one registry implementation."""
    registration = REGISTRY[stage][kind]
    properties: dict[str, Any] = {"kind": {"const": kind}}
    weights = {"deepening_factor", "deepening_value", "lookahead_factor", "reuse_level"}
    for name, default in registration.defaults.items():
        if name == "geometry":
            schema = nullable(_geometry(stage))
        elif name == "coverage":
            schema = nullable(_coverage())
        elif name == "max_cz_gates":
            schema = {
                "oneOf": [{"type": "integer", "minimum": 1}, {"const": "catalog"}]
            }
        elif name == "storage_zone_id" and kind in ("ids", "astar"):
            schema = {"oneOf": [STRING, array(STRING)]}
        elif name == "method":
            schema = {"const": kind}
        elif isinstance(default, bool):
            schema = {"type": "boolean"}
        elif isinstance(default, float) or name in weights:
            schema = dict(NUMBER)
        elif isinstance(default, int):
            schema = dict(INTEGER)
        else:
            schema = dict(STRING)
        properties[name] = {**schema, "default": deepcopy(default)}
    return obj(properties, ("kind",))


def job_schema() -> dict[str, Any]:
    """Return a Draft 2020-12 nadit.job/1 schema, with unknown keys rejected."""
    architecture = {
        "oneOf": [
            obj(
                {"family": {"const": family}, "params": info["parameters"]},
                ("family", "params"),
            )
            for family, info in describe_families().items()
        ]
    }
    document = obj(
        {
            "schema": {"const": "nadit.job/1"},
            "circuit": obj({"qasm": STRING, "gate_set": array(STRING)}, ("qasm",)),
            "device": {
                "oneOf": [
                    STRING,
                    device_schema(),
                    obj({"architecture": architecture}, ("architecture",)),
                ]
            },
            "aod": STRING,
            "gate_zones": array(
                obj(
                    {"zone": STRING, "pairs": array(fixed([STRING, STRING]))},
                    ("zone", "pairs"),
                )
            ),
            "strategy": {
                "oneOf": [item.options_schema() for item in _compilers().values()]
            },
            "entry": {
                "oneOf": [
                    obj(
                        {
                            "kind": {"const": "initial_storage"},
                            "atom_ids": nullable(array(INTEGER)),
                        },
                        ("kind",),
                    ),
                    obj(
                        {
                            "kind": {"const": "explicit"},
                            "atoms": array(fixed([INTEGER, STRING, INTEGER])),
                        },
                        ("kind", "atoms"),
                    ),
                    enola_initial_schema(),
                ]
            },
            "case": obj(
                {
                    "seed": {**INTEGER, "default": 0},
                    "overrides": {
                        "type": "object",
                        "propertyNames": {"pattern": "^[0-9]+$"},
                        "additionalProperties": {"type": "integer", "enum": [0, 1]},
                    },
                }
            ),
        },
        ("schema", "circuit", "device", "aod", "strategy"),
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "nadit.job/1",
        **document,
    }


def nonzoned_schema() -> dict[str, Any]:
    """Describe per-circuit fixed-layout shared-array compilation."""
    return obj(
        {
            "kind": {"const": "nonzoned"},
            "max_cz_gates": {"type": "integer", "minimum": 1},
        },
        ("kind",),
    )


def enola_initial_schema() -> dict[str, Any]:
    """Describe optional initial SA with a fixed, reproducible search budget."""
    return obj(
        {
            "kind": {"const": "enola_initial"},
            "array": STRING,
            "seed": {**INTEGER, "default": 0},
            "iterations": {"type": "integer", "minimum": 1, "default": 100},
            "moves_per_iteration": {"type": "integer", "minimum": 1, "default": 400},
        },
        ("kind",),
    )


def _compilers() -> dict[str, Any]:
    from ..compilers.registry import COMPILERS

    return COMPILERS


def search_smarter_schema() -> dict[str, Any]:
    """Describe named or legacy stage-based compilation including QEC mode."""
    return obj(
        {
            "kind": {"const": "search_smarter"},
            "cross_layer_reuse": {
                "type": "boolean",
                "default": False,
                "description": (
                    "QEC only: retain between known CZ layers; "
                    "restore fixed homes at window boundaries."
                ),
            },
            "mode": {
                "enum": ["adaptive", "qec"],
                "default": "adaptive",
            },
            **{
                stage: {"oneOf": [stage_schema(stage, kind) for kind in kinds]}
                for stage, kinds in REGISTRY.items()
            },
        },
        tuple(REGISTRY),
    )
