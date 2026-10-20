"""Catalogue placement with retained gate anchors and spectator evacuation."""

from dataclasses import dataclass
from math import hypot

from ..contracts import GateLayer, PlacementPlan
from ..device import Device, Position, SLMSite
from ..physical_ir import PhysicalDAG
from ..state import AODHolder, AtomLayout, AtomState, SLMHolder
from .catalog import GatePairCatalog
from .pair import PairPlacer
from .retention import prepare_gate_layout, reachable_storage_sites


@dataclass(frozen=True, slots=True)
class MultiPairPlacer:
    """Assign disjoint gates to distinct catalogue pairs and clear spectators.

    Args:
        aod_id: Transport device used to reach selected traps.
        catalog: Validated fixed pairs and shared pulse coverage.
        storage_zone_id: Zone containing return sites outside the pulse coverage.
    """

    aod_id: str
    catalog: GatePairCatalog
    storage_zone_id: str = "storage"

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
        """Choose a shared-CZ arrangement and retain selected atoms for reuse.

        Args:
            dag: Native physical operations.
            layer: Disjoint, homogeneous operations selected by the scheduler.
            layout: Current atom positions, holders, and qubit bindings.
            device: Fixed sites, transport workspace, and operation capabilities.
            reuse_qubits: Current participating qubits to retain at their
                execution gate sites for a known following CZ layer.
            following: Accepted next-layer hint; this baseline retains all candidates.
            retained_atoms: Incoming retained snapshots; only unchanged current
                participants can anchor their gate pairs.

        Returns:
            Execution and exit layouts preserving atom order and qubit bindings.
            Local operations and singleton readout use the existing pair helper.

        Raises:
            ValueError: If pair capacity or free storage is insufficient, or
                current gate-site anchors cannot satisfy the selected layer.
        """
        if not layer.operation_ids or dag.nodes[layer.operation_ids[0]].name != "cz":
            return PairPlacer(self.aod_id).place(
                dag,
                layer,
                layout,
                device,
                reuse_qubits=reuse_qubits,
                following=following,
                retained_atoms=retained_atoms,
            )
        if len(layer.operation_ids) > self.catalog.capacity:
            raise ValueError("CZ layer exceeds the gate-pair catalogue capacity.")
        participants = {
            layout.qubit_to_atom[q]
            for operation_id in layer.operation_ids
            for q in dag.nodes[operation_id].qubits
        }
        storage = reachable_storage_sites(
            device, self.aod_id, self.storage_zone_id, self.catalog.coverage
        )
        prepared, anchors = prepare_gate_layout(
            layout, storage, self.catalog, participants, retained_atoms
        )
        assignments = self._gate_assignments(dag, layer, prepared, device, anchors)
        retained_ids = {
            layout.qubit_to_atom[q] for q in reuse_qubits
        } & assignments.keys()
        retained = {atom_id: assignments[atom_id] for atom_id in retained_ids}
        exit_layout = self._storage_exit(prepared, storage, participants, retained)
        execution = _replace_atoms(exit_layout, assignments)
        return PlacementPlan(
            execution, exit_layout, prepared if prepared is not layout else None
        )

    def _storage_exit(
        self,
        layout: AtomLayout,
        sites: tuple[SLMSite, ...],
        participants: set[int],
        retained: dict[int, AtomState],
    ) -> AtomLayout:
        site_ids = {site.id for site in sites}
        relocations = [
            atom
            for atom in layout.atoms
            if atom.id not in retained
            and (
                atom.id in participants or self.catalog.coverage.contains(atom.position)
            )
            and not (
                isinstance(atom.holder, SLMHolder) and atom.holder.site_id in site_ids
            )
        ]
        moving_ids = {atom.id for atom in relocations}
        occupied = {
            atom.position
            for atom in layout.atoms
            if atom.id not in moving_ids and atom.id not in retained
        }
        initially_occupied = {atom.position for atom in layout.atoms}
        replacements = dict(retained)
        if retained:
            relocations.sort(key=lambda atom: atom.id in participants)
        for atom in relocations:
            free = [site for site in sites if site.position not in occupied]
            if not free:
                raise ValueError("Insufficient free storage outside CZ pulse coverage.")
            selected = min(
                free,
                key=lambda site: (
                    atom.id not in participants and site.position in initially_occupied,
                    _distance(atom.position, site.position),
                    site.id,
                ),
            )
            replacements[atom.id] = _at_site(atom.id, selected)
            occupied.add(selected.position)
        return _replace_atoms(layout, replacements) if replacements else layout

    def _gate_assignments(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        entry_layout: AtomLayout,
        device: Device,
        retained_ids: frozenset[int],
    ) -> dict[int, AtomState]:
        workspace = device.aod(self.aod_id).workspace
        available = {
            index: pair
            for index, pair in enumerate(self.catalog.pairs)
            if all(workspace.contains(site.position) for site in pair)
        }
        if len(layer.operation_ids) > len(available):
            raise ValueError(
                "Reachable gate-pair capacity is smaller than the CZ layer."
            )
        locations = {
            site.id: (index, side)
            for index, pair in enumerate(self.catalog.pairs)
            for side, site in enumerate(pair)
        }
        assignments: dict[int, AtomState] = {}
        unanchored: list[tuple[AtomState, AtomState]] = []
        for operation_id in layer.operation_ids:
            operation = dag.nodes[operation_id]
            first, second = (
                entry_layout.atom(entry_layout.qubit_to_atom[q])
                for q in operation.qubits
            )
            anchor = _anchor_pair(
                first, second, locations, self.catalog.pairs, retained_ids
            )
            if anchor is None:
                unanchored.append((first, second))
                continue
            index, first_site, second_site = anchor
            if index not in available:
                raise ValueError(
                    "Gate anchors require the same or an unreachable pair."
                )
            del available[index]
            assignments[first.id] = _at_site(first.id, first_site)
            assignments[second.id] = _at_site(second.id, second_site)
        for first, second in unanchored:
            candidates = (
                (
                    _distance(first.position, a.position)
                    + _distance(second.position, b.position),
                    index,
                    orientation,
                    a,
                    b,
                )
                for index, pair in available.items()
                for orientation, (a, b) in enumerate((pair, pair[::-1]))
            )
            _, selected_index, _, first_site, second_site = min(
                candidates, key=lambda item: item[:3]
            )
            assignments[first.id] = _at_site(first.id, first_site)
            assignments[second.id] = _at_site(second.id, second_site)
            del available[selected_index]
        return assignments


def _anchor_pair(
    first: AtomState,
    second: AtomState,
    locations: dict[str, tuple[int, int]],
    pairs: tuple[tuple[SLMSite, SLMSite], ...],
    retained_ids: frozenset[int],
) -> tuple[int, SLMSite, SLMSite] | None:
    first_anchor = (
        locations.get(first.holder.site_id)
        if first.id in retained_ids and isinstance(first.holder, SLMHolder)
        else None
    )
    second_anchor = (
        locations.get(second.holder.site_id)
        if second.id in retained_ids and isinstance(second.holder, SLMHolder)
        else None
    )
    if first_anchor is not None:
        index, side = first_anchor
        if second_anchor is not None and second_anchor[0] != index:
            raise ValueError("A CZ gate has anchors in different catalogue pairs.")
        return index, pairs[index][side], pairs[index][1 - side]
    if second_anchor is not None:
        index, side = second_anchor
        return index, pairs[index][1 - side], pairs[index][side]
    return None


def _distance(first: Position, second: Position) -> float:
    return hypot(first.x - second.x, first.y - second.y)


def _at_site(atom_id: int, site: SLMSite) -> AtomState:
    return AtomState(atom_id, site.position, SLMHolder(site.id))


def _replace_atoms(
    layout: AtomLayout, replacements: dict[int, AtomState]
) -> AtomLayout:
    atoms = tuple(replacements.get(atom.id, atom) for atom in layout.atoms)
    occupied_aods = {
        atom.holder.aod_id for atom in atoms if isinstance(atom.holder, AODHolder)
    }
    grids = tuple(grid for grid in layout.aod_grids if grid.aod_id in occupied_aods)
    return AtomLayout(atoms, grids, layout.qubit_to_atom)
