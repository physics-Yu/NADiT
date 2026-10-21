"""Machine-readable discovery of architecture and compiler configuration."""

from copy import deepcopy
from typing import Any

from .architectures import describe_families
from .job import JOB_SCHEMA
from .registry import REGISTRY
from .schemas.job import stage_schema
from .serialization import RESULT_SCHEMA, RESULT_SCHEMA_V2


def describe() -> dict[str, Any]:
    """Return versioned families and every registered stage's configuration."""
    return {
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
