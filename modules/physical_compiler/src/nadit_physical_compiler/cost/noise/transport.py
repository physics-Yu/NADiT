"""Configurable transport loss; detailed dynamics implement the same protocol."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import exp, hypot
from typing import Any, Protocol

from ...activity import ActivityEvent


@dataclass(frozen=True, slots=True)
class TransportEffect:
    """Conditional survival and Pauli weights for one atom's movement event."""

    survival: float = 1.0
    pauli: tuple[float, ...] = (0.0, 0.0, 0.0)


class TransportNoiseModel(Protocol):
    """Extension interface: evaluate an atom within a timed transport event."""

    def evaluate(self, event: ActivityEvent, atom_index: int) -> TransportEffect:
        """Return loss and conditional computational noise without mutating state."""
        ...


class RateTransportModel:
    """Independent per-event losses plus distance/time hazard during MOVE."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        """Accept resolved rates/v1 parameters in um and us."""
        self.params = params

    def evaluate(self, event: ActivityEvent, atom_index: int) -> TransportEffect:
        """Estimate one atom using endpoints; no trajectory/velocity simulation."""
        p = self.params
        if event.kind in ("Load", "Store"):
            return TransportEffect(1 - p[event.kind.lower() + "_loss"])
        if event.kind != "Move":
            return TransportEffect()
        x, y = event.starts[atom_index]
        tx, ty = event.ends[atom_index]
        hazard = (
            p["loss_per_um"] * hypot(tx - x, ty - y)
            + p["loss_per_us"] * event.duration_us
        )
        return TransportEffect(
            (1 - p["move_loss"]) * exp(-hazard), tuple(p["move_pauli"])
        )
