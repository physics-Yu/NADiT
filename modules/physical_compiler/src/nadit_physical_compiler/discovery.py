"""Machine-readable discovery of architecture and compiler configuration."""

from copy import deepcopy
from typing import Any

from .architectures import describe_families
from .compilers.registry import describe_compilers
from .job import JOB_SCHEMA
from .registry import REGISTRY
from .schemas.job import enola_initial_schema, stage_schema
from .serialization import RESULT_SCHEMA, RESULT_SCHEMA_V2


def describe() -> dict[str, Any]:
    """Return versioned families and every registered stage's configuration."""
    from .cost.additive import DEFAULTS
    from .cost.hsim import HSIM_COMMIT
    from .cost.noise.config import CZ_BACKENDS, SINGLE_BACKENDS
    from .cost.noise.config import DEFAULTS as NOISE_DEFAULTS

    return {
        "entry_providers": {
            "explicit": {
                "description": "User-supplied atom/site/qubit mapping; no search."
            },
            "initial_storage": {
                "description": "Existing Search Smarter storage placement."
            },
            "enola_initial": {
                "options": enola_initial_schema(),
                "description": (
                    "One initial ENOLA SA run; each CZ layer returns to this reference."
                ),
            },
        },
        "compilers": describe_compilers(),
        "compiler_strategies": describe_compilers(),
        "cost_models": {
            "noise/v1": {
                "defaults": deepcopy(NOISE_DEFAULTS),
                "units": "us, um; upstream gate calibration uses s, Hz or MHz",
                "single_backends": SINGLE_BACKENDS,
                "cz_backends": CZ_BACKENDS,
                "transport_backends": ["rates/v1"],
                "export_schema": "nadit.noise/1",
                "scope": "physical channel configuration and independent loss estimate",
            },
            "additive/v1": {
                "defaults": deepcopy(DEFAULTS),
                "units": "us, um",
                "scope": "independent log-additive estimate",
            },
            "hsim-aqc": {
                "commit": HSIM_COMMIT,
                "optional_extra": "cost-hsim",
                "single_backends": ["ideal", "coherent_overrotation"],
                "cz_backends": ["ideal", "four_level", "open_four_level"],
                "scope": "gate-product proxy with additive background",
            },
        },
        "experiments": {
            "schema": "nadit.experiment/1",
            "suite_schema": "nadit.suite/1",
            "commands": ["plan", "run", "status", "summarize", "cost"],
            "statuses": [
                "ok",
                "unsupported",
                "invalid_job",
                "compile_error",
                "search_exhausted",
                "timeout",
                "validation_failed",
            ],
        },
        "architectures": describe_families(),
        "strategies": {
            stage: {
                kind: {
                    "options": stage_schema(stage, kind),
                    "defaults": deepcopy(registration.defaults),
                    "supports": (registration.supports.__doc__ or "").strip(),
                }
                for kind, registration in implementations.items()
            }
            for stage, implementations in REGISTRY.items()
        },
        "schemas": {
            "job": JOB_SCHEMA,
            "result": RESULT_SCHEMA_V2,
            "readable_results": [RESULT_SCHEMA, RESULT_SCHEMA_V2],
        },
    }
