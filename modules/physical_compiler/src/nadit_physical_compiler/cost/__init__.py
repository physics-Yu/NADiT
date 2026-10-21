"""Validated-result post-processing, independent of compiler execution."""

import json
from collections.abc import Mapping
from typing import Any

from ..activity import AtomActivity, derive_activity
from ..device import loads_device
from ..serialization import decode_dag, decode_layout, decode_result
from ..validation import validate_result
from .additive import AdditiveModel
from .base import CostModel, CostReport, UnvalidatedResult, require_validated

__all__ = [
    "CostModel",
    "CostReport",
    "UnvalidatedResult",
    "evaluate",
    "prepare",
    "model_for",
]


def prepare(document: Mapping[str, Any]) -> AtomActivity:
    """Revalidate execution content once and derive activity for reuse by models."""
    require_validated(document)
    checked = validate_result(
        decode_result(document),
        entry_state=decode_layout(document["entry_state"]),
        device=loads_device(json.dumps(document["device"])),
        dag=decode_dag(document["dag"]),
    )
    if not checked["ok"]:
        raise UnvalidatedResult(
            f"Cost rejected invalid execution content: {checked['failure']}"
        )
    return derive_activity(document)


def model_for(name: str) -> CostModel:
    """Resolve a built-in or optional model without importing optional code eagerly."""
    if name in ("additive", "additive/v1"):
        return AdditiveModel()
    if name == "hsim-aqc":
        from .hsim import HsimModel

        return HsimModel()
    raise ValueError(f"Unknown cost model {name!r}.")


def evaluate(
    document: Mapping[str, Any],
    *,
    model: str = "additive",
    params: Mapping[str, Any] | None = None,
) -> CostReport:
    """Evaluate a saved result under new noise parameters without recompiling."""
    activity = prepare(document)
    return model_for(model).evaluate(activity, document, params or {})
