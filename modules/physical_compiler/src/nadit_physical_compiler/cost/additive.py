"""Explicit log-additive reference estimate with distance-only heating."""

from collections.abc import Mapping
from math import exp, hypot, log1p
from typing import Any

from ..activity import AtomActivity
from ..job_fields import keys_at, number_at
from ..provenance import canonical_hash
from .base import CostReport, require_validated, result_hash

DEFAULTS: dict[str, Any] = {
    "cz_infidelity": 0.0,
    "single_infidelity": 0.0,
    "readout_infidelity": 0.0,
    "reset_infidelity": 0.0,
    "transfer_loss": 0.0,
    "slm_t2_us": None,
    "aod_t2_us": None,
    "heating_per_um": 0.0,
    "heating_us_per_um2": 0.0,
}


def resolved_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Validate explicit probabilities, T2 times, and nonnegative heating rates."""
    keys_at(dict(params), set(DEFAULTS), "cost.params")
    result = {**DEFAULTS, **params}
    for name, raw in result.items():
        if name.endswith("t2_us") and raw is None:
            continue
        value = number_at(raw, f"cost.params.{name}")
        if (
            value < 0
            or (name.endswith("t2_us") and value == 0)
            or ((name.endswith("infidelity") or name == "transfer_loss") and value >= 1)
        ):
            raise ValueError(f"cost.params.{name}: outside its supported range.")
    return result


class AdditiveModel:
    """Multiply independent event survival factors and exponential idle terms."""

    name = "additive"
    version = "v1"

    def evaluate(
        self,
        activity: AtomActivity,
        result: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> CostReport:
        """Return deterministic log contributions using a shared activity record."""
        require_validated(result)
        p = resolved_params(params)
        counts = {
            "cz": sum(
                len(event.pairs) for event in activity.events if event.kind == "CZPulse"
            ),
            "single": sum(atom.rotation_count for atom in activity.per_atom),
            "readout": sum(atom.measure_count for atom in activity.per_atom),
            "reset": sum(atom.reset_count for atom in activity.per_atom),
            "transfer": sum(atom.transfers for atom in activity.per_atom),
        }
        terms = {
            key: count
            * log1p(-p["transfer_loss" if key == "transfer" else key + "_infidelity"])
            for key, count in counts.items()
        }
        terms["idle"] = -sum(
            (interval.end_us - interval.start_us) / p[interval.holder + "_t2_us"]
            for atom in activity.per_atom
            for interval in atom.idle
            if p[interval.holder + "_t2_us"] is not None
        )
        terms["heating"] = 0.0
        for event in activity.events:
            if event.kind != "Move":
                continue
            for source, target in zip(event.starts, event.ends, strict=True):
                distance = hypot(target[0] - source[0], target[1] - source[1])
                terms["heating"] -= p["heating_per_um"] * distance
                if p["heating_us_per_um2"] and distance:
                    if event.duration_us <= 0:
                        raise ValueError(
                            "Duration-dependent heating needs positive MOVE duration."
                        )
                    terms["heating"] -= (
                        p["heating_us_per_um2"] * distance**2 / event.duration_us
                    )
        total = sum(terms.values())
        return {
            "model": self.name,
            "version": self.version,
            "params_hash": canonical_hash(p),
            "params": p,
            "result_hash": result_hash(result),
            "metrics": {
                "log_fidelity": total,
                "fidelity": exp(total),
                "contributions": terms,
            },
            "warnings": [
                "Independent error-product estimate; not quantum-state simulation."
            ],
        }
