"""Fixed storage homes with optional stays at existing gate catalogue sites."""

from dataclasses import dataclass, replace

from ..contracts import GateLayer, PlacementPlan
from ..device import Device
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout, AtomState
from .heuristic import HeuristicPlacer
from .multipair import MultiPairPlacer, _replace_atoms


def home_with_stays(home: AtomLayout, stays: dict[int, AtomState]) -> AtomLayout:
    """Keep each original home binding except explicitly selected SLM stays."""
    return replace(home, atoms=tuple(stays.get(atom.id, atom) for atom in home.atoms))


@dataclass(frozen=True, slots=True)
class FixedReusePlacer:
    """Reuse execution placement while eliminating adaptive storage reassignment."""

    base: HeuristicPlacer | MultiPairPlacer
    home: AtomLayout

    def __post_init__(self) -> None:
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
        """Clear spectators home, place gates, and retain selected endpoints."""
        cz = (
            bool(layer.operation_ids) and dag.nodes[layer.operation_ids[0]].name == "cz"
        )
        prepared = layout
        if cz:
            participants = {
                layout.qubit_to_atom[q]
                for index in layer.operation_ids
                for q in dag.nodes[index].qubits
            }
            valid = {
                a.id
                for a in retained_atoms
                if a.id in participants and layout.atom(a.id) == a
            }
            homes = {a.id: a for a in self.home.atoms}
            clear = {
                a.id: homes[a.id]
                for a in layout.atoms
                if self.base.catalog.coverage.contains(a.position) and a.id not in valid
            }
            if clear:
                prepared = _replace_atoms(layout, clear)
        plan = self.base.place(
            dag,
            layer,
            prepared,
            device,
            reuse_qubits=reuse_qubits,
            following=following,
            retained_atoms=retained_atoms,
        )
        execution = plan.execution_layout
        if cz:
            stays = (
                {
                    execution.qubit_to_atom[q]: execution.atom(
                        execution.qubit_to_atom[q]
                    )
                    for q in reuse_qubits
                }
                if following is not None
                else {}
            )
        else:
            stays = {a.id: a for a in retained_atoms if execution.atom(a.id) == a}
        return PlacementPlan(
            execution,
            home_with_stays(self.home, stays),
            plan.preparation_layout or (prepared if prepared != layout else None),
        )
