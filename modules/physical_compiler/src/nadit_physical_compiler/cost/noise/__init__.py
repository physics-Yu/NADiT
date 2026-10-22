"""Modular physical channels and non-fault-tolerant loss post-processing."""

from collections.abc import Mapping
from typing import Any

from ...activity import ActivityEvent, AtomActivity
from ...provenance import canonical_hash
from ...serialization import decode_layout
from ..base import CostReport, require_validated, result_hash
from ..hsim import HSIM_COMMIT
from .config import resolve
from .exposure import add_exposure
from .gates import GateChannels
from .report import NoiseReport, event_context, log_probability
from .transport import RateTransportModel, TransportNoiseModel

__all__ = ["NoiseModel", "TransportNoiseModel"]


def _gate(
    event: ActivityEvent,
    context: dict[str, Any],
    p: dict[str, Any],
    gates: GateChannels,
    report: NoiseReport,
) -> None:
    cz = event.kind == "CZPulse"
    config = (
        p["cz"]
        if cz
        else p["single_overrides"].get(event.parameters.get("axis", "U"), p["single"])
    )
    effect = gates.evaluate(config, event)
    if effect.record_id is not None:
        context = {**context, "upstream_record_id": effect.record_id}
    targets = event.pairs if cz else tuple((a,) for a in event.atoms)
    for atoms in targets:
        report.channel(
            "PAULI_CHANNEL_2" if cz else "PAULI_CHANNEL_1",
            effect.pauli,
            atoms,
            context,
            placement="before",
            diagnostic=effect.diagnostics,
        )
        report.no_fault_log += log_probability(effect.computational_survival)
        report.fidelity_logs["cz" if cz else "single"] += log_probability(
            effect.fidelity
        )
        if effect.diagnostics or effect.computational_survival < 1:
            report.gates.append(
                {
                    **context,
                    "atoms": list(atoms),
                    **effect.diagnostics,
                    "computational_survival_probability": effect.computational_survival,
                    "physical_atom_loss_inferred": False,
                }
            )
        if cz:
            for atom in atoms:
                report.loss(
                    atom,
                    1 - p["cz_atom_loss"],
                    {**context, "cause": "configured_cz_atom_loss"},
                )
    if cz:
        participants = {a for pair in event.pairs for a in pair}
        for atom in event.atoms:
            if atom not in participants:
                weights = p["spectator_pauli"]
                report.channel("PAULI_CHANNEL_1", weights, (atom,), context)
                report.fidelity_logs["spectator"] += log_probability(
                    1 - 2 * sum(weights) / 3
                )


class NoiseModel:
    """Export configurable channels plus independent physical error/loss estimates."""

    name = "noise"
    version = "v1.1"

    def evaluate(
        self,
        activity: AtomActivity,
        result: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> CostReport:
        """Consume validated shared activity without compiling or sampling branches."""
        require_validated(result)
        p = resolve(params)
        layout = decode_layout(result["entry_state"])
        report = NoiseReport(
            {a: q for q, a in enumerate(layout.qubit_to_atom)},
            [a.atom_id for a in activity.per_atom],
        )
        gates = GateChannels()
        transport: TransportNoiseModel = RateTransportModel(p["transport"])
        for index, event in enumerate(activity.events):
            context = event_context(event, index)
            if event.kind in ("CZPulse", "Rotation", "LocalU", "GlobalRY"):
                _gate(event, context, p, gates, report)
            elif event.kind in ("Load", "Move", "Store"):
                for offset, atom in enumerate(event.atoms):
                    effect = transport.evaluate(event, offset)
                    report.loss(
                        atom, effect.survival, {**context, "cause": event.kind.lower()}
                    )
                    report.channel("PAULI_CHANNEL_1", effect.pauli, (atom,), context)
                    report.fidelity_logs["transport_pauli"] += log_probability(
                        1 - 2 * sum(effect.pauli) / 3
                    )
            elif event.kind in ("Measure", "Reset"):
                measure = event.kind == "Measure"
                weight = p["readout_flip" if measure else "reset_flip"]
                for offset, atom in enumerate(event.atoms):
                    metadata = {**context}
                    if measure:
                        metadata["measurement_id"] = event.measurement_ids[offset]
                    report.channel(
                        "M" if measure else "X_ERROR",
                        [weight],
                        (atom,),
                        metadata,
                        placement="measurement_argument" if measure else "after",
                    )
                    report.fidelity_logs["readout" if measure else "reset"] += (
                        log_probability(1 - weight)
                    )
        add_exposure(activity, p, report)
        metrics, noise = report.finish()
        noise["upstream_records"] = gates.records
        return {
            "model": self.name,
            "version": self.version,
            "params": p,
            "params_hash": canonical_hash(p),
            "result_hash": result_hash(result),
            "upstream": {"hsim_aqc_commit": HSIM_COMMIT} if gates.uses_hsim else {},
            "metrics": metrics,
            "noise": noise,
            "warnings": [
                "Independent physical error product; "
                "not logical fidelity or state simulation.",
                "Noise channels describe the compiled measurement branch; "
                "readout noise does not reschedule feedback.",
                "Joint CZ computational leakage is not independent physical "
                "atom loss or heralded erasure.",
                "No initial preparation error is inferred; "
                "reset noise applies only to explicit Reset actions.",
            ],
        }
