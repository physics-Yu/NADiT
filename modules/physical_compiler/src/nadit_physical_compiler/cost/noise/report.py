"""Event export and independent products, with physical loss kept separate."""

from collections import defaultdict
from math import exp, isfinite, log
from typing import Any

from ...activity import ActivityEvent

PAULI_2_ORDER = [
    "IX",
    "IY",
    "IZ",
    "XI",
    "XX",
    "XY",
    "XZ",
    "YI",
    "YX",
    "YY",
    "YZ",
    "ZI",
    "ZX",
    "ZY",
    "ZZ",
]


def log_probability(p: float) -> float:
    """Allow certain loss internally; report serialization converts -inf to null."""
    return log(p) if p > 0 else float("-inf")


def event_context(event: ActivityEvent, event_index: int) -> dict[str, Any]:
    """Locate a channel in the original action timeline and physical DAG."""
    return {
        "event_id": event_index,
        "step_index": event.step_index,
        "action_index": event.action_index,
        "source_ids": list(event.source_ids),
        "kind": event.kind,
        "start_us": event.start_us,
        "duration_us": event.duration_us,
    }


class NoiseReport:
    """Collect channel records, pair diagnostics and per-atom survival products."""

    def __init__(self, atom_to_qubit: dict[int, int], atoms: list[int]) -> None:
        """Initialize every retained input atom, including atoms with no operations."""
        self.atom_to_qubit = atom_to_qubit
        self.channels: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.loss_events: list[dict[str, Any]] = []
        self.atom_logs = dict.fromkeys(atoms, 0.0)
        self.fidelity_logs: dict[str, float] = defaultdict(float)
        self.no_fault_log = 0.0

    def channel(
        self,
        instruction: str,
        weights: list[float] | tuple[float, ...],
        atoms: tuple[int, ...],
        context: dict[str, Any],
        *,
        placement: str = "after",
        diagnostic: dict[str, Any] | None = None,
    ) -> None:
        """Append a nonzero channel and accumulate its identity probability."""
        self.no_fault_log += log_probability(max(0.0, 1 - sum(weights)))
        if not any(weights):
            return
        self.channels.append(
            {
                **context,
                "instruction": instruction,
                "probabilities": list(weights),
                "atoms": list(atoms),
                "qubits": [self.atom_to_qubit.get(a) for a in atoms],
                "placement": placement,
                "diagnostics": diagnostic or {},
            }
        )

    def loss(self, atom: int, survival: float, context: dict[str, Any]) -> None:
        """Apply one physical atom-loss opportunity conditional on prior survival."""
        term = log_probability(survival)
        self.atom_logs[atom] += term
        self.fidelity_logs["physical_loss"] += term
        self.no_fault_log += term
        if survival < 1:
            self.loss_events.append(
                {**context, "atom": atom, "survival_probability": survival}
            )

    def finish(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return JSON-safe metrics and a Stim-style channel configuration."""
        total = sum(self.fidelity_logs.values())
        survival = {a: exp(v) for a, v in self.atom_logs.items()}
        metrics = {
            "fidelity": exp(total),
            "log_fidelity": total if isfinite(total) else None,
            "fidelity_kind": "physical_error_product_proxy",
            "no_fault_probability": exp(self.no_fault_log),
            "contributions": {
                k: v if isfinite(v) else None for k, v in self.fidelity_logs.items()
            },
        }
        loss = {
            "per_atom": [
                {"atom": a, "survival_probability": s, "loss_probability": 1 - s}
                for a, s in survival.items()
            ],
            "expected_lost_atoms": sum(1 - s for s in survival.values()),
            "all_atoms_survive_probability": exp(sum(self.atom_logs.values())),
            "events": self.loss_events,
        }
        return metrics, {
            "schema": "nadit.noise/1",
            "pauli_channel_1_order": ["X", "Y", "Z"],
            "pauli_channel_2_order": PAULI_2_ORDER,
            "channels": self.channels,
            "gate_diagnostics": self.gates,
            "loss": loss,
        }
