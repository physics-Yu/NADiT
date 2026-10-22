"""ENOLA-inspired dual candidates and strict shared-array transport batches."""

from dataclasses import dataclass, replace
from math import hypot

from ..contracts import GateLayer
from ..device import Device, SLMSite
from ..physical_ir import PhysicalDAG
from ..placement import GatePairCatalog
from ..state import AtomLayout, AtomState, SLMHolder
from ..transport.occupancy import Occupancy
from ..transport.rules import MoveRequest, TransportRules, rules_for


@dataclass(frozen=True, slots=True)
class Candidate:
    """One directional choice for a native CZ operation."""

    operation_id: int
    move: MoveRequest
    destination: SLMSite


@dataclass(frozen=True, slots=True)
class LayerPlan:
    """Ordered arrival batches and the layout used by the whole-layer pulse."""

    batches: tuple[tuple[Candidate, ...], ...]
    execution_layout: AtomLayout


def candidates_for(
    dag: PhysicalDAG, layer: GateLayer, reference: AtomLayout, catalog: GatePairCatalog
) -> tuple[Candidate, ...]:
    """Generate both directions, longest home-to-home distance first.

    Each reference atom uses a declared SLM cell. Isolated cells containing
    both operands already execute without transport. Other doubly occupied
    cells would cause an unintended global-pulse interaction.
    """
    partners = {a.id: b for pair in catalog.pairs for a, b in (pair, pair[::-1])}
    occupied: dict[str, int] = {}
    for atom in reference.atoms:
        if (
            not isinstance(atom.holder, SLMHolder)
            or atom.holder.site_id not in partners
        ):
            raise ValueError("Non-zoned reference atoms require declared SLM cells.")
        occupied[atom.holder.site_id] = atom.id
    if reference.aod_grids:
        raise ValueError("Non-zoned reference layout requires an empty AOD.")
    pairs = [
        (
            index,
            tuple(
                reference.atom(reference.qubit_to_atom[q])
                for q in dag.nodes[index].qubits
            ),
        )
        for index in layer.operation_ids
    ]
    expected = {frozenset(a.id for a in atoms) for _, atoms in pairs}
    for first, second in catalog.pairs:
        if first.id in occupied and second.id in occupied:
            if frozenset((occupied[first.id], occupied[second.id])) not in expected:
                raise ValueError(
                    "Non-zoned reference contains an unintended occupied pair."
                )
    ordered = sorted(
        pairs,
        key=lambda item: (
            -hypot(
                item[1][0].position.x - item[1][1].position.x,
                item[1][0].position.y - item[1][1].position.y,
            )
        ),
    )
    result = []
    for index, atoms in ordered:
        for mover, fixed in (atoms, atoms[::-1]):
            assert isinstance(fixed.holder, SLMHolder)
            destination = partners[fixed.holder.site_id]
            if occupied.get(destination.id) == mover.id:
                continue
            result.append(
                Candidate(
                    index,
                    MoveRequest(mover.id, mover.position, destination.position),
                    destination,
                )
            )
    return tuple(result)


def conflict_map(
    candidates: tuple[Candidate, ...], rules: TransportRules
) -> tuple[frozenset[int], ...]:
    """Index movement choices, retaining mutually exclusive dual candidates."""
    edges: list[set[int]] = [set() for _ in candidates]
    for i, first in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            second = candidates[j]
            if first.operation_id == second.operation_id or not rules.compatible(
                first.move, second.move
            ):
                edges[i].add(j)
                edges[j].add(i)
    return tuple(frozenset(edge) for edge in edges)


def plan_layer(
    dag: PhysicalDAG,
    layer: GateLayer,
    reference: AtomLayout,
    device: Device,
    catalog: GatePairCatalog,
    aod_id: str,
) -> LayerPlan:
    """Choose strict batches without invoking full action or semantic validators.

    Pairwise compatibility follows ENOLA's greedy independent-set structure.
    Capacity and occupancy closure are admitted on the whole selected set.
    A selected gate removes both directional alternatives from the pending set.
    """
    candidates = candidates_for(dag, layer, reference, catalog)
    rules = rules_for(device.aod(aod_id), device.timing)
    selector = _Selector(
        candidates,
        conflict_map(candidates, rules),
        rules,
        Occupancy({a.id: a.position for a in reference.atoms}),
    )
    pending = set(range(len(candidates)))
    atoms = {a.id: a for a in reference.atoms}
    batches = []
    while pending:
        indices = selector.select(pending)
        batch = tuple(candidates[i] for i in indices)
        completed = {c.operation_id for c in batch}
        pending = {i for i in pending if candidates[i].operation_id not in completed}
        for candidate in batch:
            selector.occupied.remove(candidate.move.atom_id)
        for candidate in batch:
            site, atom_id = candidate.destination, candidate.move.atom_id
            atoms[atom_id] = AtomState(atom_id, site.position, SLMHolder(site.id))
            selector.occupied.add(atom_id, site.position)
        batches.append(batch)
    return LayerPlan(
        tuple(batches),
        replace(reference, atoms=tuple(atoms[a.id] for a in reference.atoms)),
    )


@dataclass
class _Selector:
    candidates: tuple[Candidate, ...]
    conflicts: tuple[frozenset[int], ...]
    rules: TransportRules
    occupied: Occupancy

    def select(self, pending: set[int]) -> tuple[int, ...]:
        selected: set[int] = set()
        for i in sorted(pending):
            if i in selected or self.conflicts[i] & selected:
                continue
            closed = self.close(selected | {i}, pending)
            if closed is not None:
                selected = closed
        if not selected:
            raise ValueError(
                "No non-zoned strict batch fits the Device and occupied cells."
            )
        return tuple(sorted(selected))

    def close(self, selected: set[int], pending: set[int]) -> set[int] | None:
        by_atom = {self.candidates[i].move.atom_id: i for i in pending}
        while True:
            if any(self.conflicts[i] & selected for i in selected):
                return None
            moves = tuple(self.candidates[i].move for i in sorted(selected))
            if not self.rules.can_fit(moves):
                return None
            source, target, _ = self.rules.frames(moves)
            blockers = set().union(
                *(
                    self.occupied.at_grid(
                        tuple(c.coordinate for c in grid.columns),
                        tuple(r.coordinate for r in grid.rows),
                    )
                    for grid in (source, target)
                )
            ) - {m.atom_id for m in moves}
            if not blockers:
                return selected
            if not blockers <= by_atom.keys():
                return None
            selected |= {by_atom[atom] for atom in blockers}
