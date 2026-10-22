"""Holder residence loss and Markovian dephasing outside modeled gates."""

from math import exp, expm1
from typing import Any

from ...activity import AtomActivity
from .report import NoiseReport


def add_exposure(
    activity: AtomActivity, params: dict[str, Any], report: NoiseReport
) -> None:
    """Apply holder loss throughout residence and T2 on selected exposure intervals."""
    config = params["exposure"]
    for atom in activity.per_atom:
        for index, interval in enumerate((*atom.idle, *atom.busy)):
            duration = interval.end_us - interval.start_us
            context = {
                "interval_id": f"{atom.atom_id}:{index}",
                "kind": interval.kind,
                "holder": interval.holder,
                "start_us": interval.start_us,
                "duration_us": duration,
            }
            rate = params["transport"][interval.holder + "_loss_per_us"]
            report.loss(
                atom.atom_id,
                exp(-duration * rate),
                {**context, "cause": "holder_lifetime"},
            )
            t2 = config[interval.holder + "_t2_us"]
            selected = interval.kind == "idle" or (
                config["include_transport"]
                and interval.kind in ("Load", "Move", "Store")
            )
            if selected and t2 is not None:
                exponent = -duration / t2
                report.channel(
                    "Z_ERROR", [-0.5 * expm1(exponent)], (atom.atom_id,), context
                )
                report.fidelity_logs["coherence"] += exponent
