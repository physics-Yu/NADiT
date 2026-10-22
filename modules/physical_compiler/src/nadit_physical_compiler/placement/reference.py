"""Adapt execution placement to an exact per-layer reference boundary."""

from dataclasses import dataclass, replace

from ..contracts import GateLayer, PlacementPlan, Placer
from ..device import Device
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout, AtomState
from .heuristic import HeuristicPlacer


@dataclass(frozen=True, slots=True)
class ReferencePlacer:
    """Keep execution placement while restoring the incoming atom/site mapping.

    Args:
        base: Execution placer. Heuristic placement skips its storage search.
    """

    base: Placer

    def __post_init__(self) -> None:
        if isinstance(self.base, HeuristicPlacer):
            object.__setattr__(self, "base", replace(self.base, return_to_entry=True))

    def place(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        reuse_qubits: tuple[int, ...] = (),
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> PlacementPlan:
        """Choose execution sites without retention and return to the input layout."""
        plan = self.base.place(dag, layer, layout, device)
        return replace(plan, exit_layout=layout)
