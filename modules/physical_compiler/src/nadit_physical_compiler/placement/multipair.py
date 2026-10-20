"""Deterministic catalogue placement with storage returns after each CZ layer."""

from dataclasses import dataclass
from math import hypot

from ..contracts import GateLayer, PlacementPlan
from ..device import Device, Position, SLMSite
from ..physical_ir import PhysicalDAG
from ..state import AODHolder, AtomLayout, AtomState, SLMHolder
from .catalog import GatePairCatalog
from .pair import PairPlacer


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
    ) -> PlacementPlan:
        """Choose one shared-CZ arrangement and its storage return layout.

        Args:
            dag: Native physical operations.
            layer: Disjoint, homogeneous operations selected by the scheduler.
            layout: Current atom positions, holders, and qubit bindings.
            device: Fixed sites, transport workspace, and operation capabilities.
            reuse_qubits: Interface hint; this baseline returns every CZ
                participant to storage and does not retain gate-site assignments.

        Returns:
            Execution and exit layouts preserving atom order and qubit bindings.
            Local operations and singleton readout use the existing pair helper.

        Raises:
            ValueError: If pair capacity or free storage is insufficient.
        """
        if not layer.operation_ids or dag.nodes[layer.operation_ids[0]].name != "cz":
            return PairPlacer(self.aod_id).place(
                dag, layer, layout, device, reuse_qubits=reuse_qubits
            )
        if len(layer.operation_ids) > self.catalog.capacity:
            raise ValueError("CZ layer exceeds the gate-pair catalogue capacity.")
        participants = {
            layout.qubit_to_atom[q]
            for operation_id in layer.operation_ids
            for q in dag.nodes[operation_id].qubits
        }
        exit_layout = self._storage_exit(layout, device, participants)
        execution = self._execution_layout(dag, layer, layout, exit_layout, device)
        return PlacementPlan(execution, exit_layout)

    def _storage_exit(
        self, layout: AtomLayout, device: Device, participants: set[int]
    ) -> AtomLayout:
        zone = next(
            (item for item in device.zones if item.id == self.storage_zone_id), None
        )
        if zone is None:
            raise ValueError(f"Missing storage zone {self.storage_zone_id!r}.")
        workspace = device.aod(self.aod_id).workspace
        sites = tuple(
            site
            for site in device.slm_sites
            if zone.bounds.contains(site.position)
            and not self.catalog.coverage.contains(site.position)
            and workspace.contains(site.position)
        )
        site_ids = {site.id for site in sites}
        relocations = [
            atom
            for atom in layout.atoms
            if (
                atom.id in participants or self.catalog.coverage.contains(atom.position)
            )
            and not (
                isinstance(atom.holder, SLMHolder) and atom.holder.site_id in site_ids
            )
        ]
        moving_ids = {atom.id for atom in relocations}
        occupied = {atom.position for atom in layout.atoms if atom.id not in moving_ids}
        replacements: dict[int, AtomState] = {}
        for atom in relocations:
            free = [site for site in sites if site.position not in occupied]
            if not free:
                raise ValueError("Insufficient free storage outside CZ pulse coverage.")
            selected = min(
                free,
                key=lambda site: (_distance(atom.position, site.position), site.id),
            )
            replacements[atom.id] = _at_site(atom.id, selected)
            occupied.add(selected.position)
        return _replace_atoms(layout, replacements) if replacements else layout

    def _execution_layout(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        entry_layout: AtomLayout,
        exit_layout: AtomLayout,
        device: Device,
    ) -> AtomLayout:
        workspace = device.aod(self.aod_id).workspace
        available = [
            (index, pair)
            for index, pair in enumerate(self.catalog.pairs)
            if all(workspace.contains(site.position) for site in pair)
        ]
        if len(layer.operation_ids) > len(available):
            raise ValueError(
                "Reachable gate-pair capacity is smaller than the CZ layer."
            )
        replacements: dict[int, AtomState] = {}
        for operation_id in layer.operation_ids:
            operation = dag.nodes[operation_id]
            first, second = (
                entry_layout.atom(entry_layout.qubit_to_atom[q])
                for q in operation.qubits
            )
            candidates = (
                (
                    _distance(first.position, a.position)
                    + _distance(second.position, b.position),
                    index,
                    orientation,
                    a,
                    b,
                )
                for index, pair in available
                for orientation, (a, b) in enumerate((pair, pair[::-1]))
            )
            _, selected_index, _, first_site, second_site = min(
                candidates, key=lambda item: item[:3]
            )
            replacements[first.id] = _at_site(first.id, first_site)
            replacements[second.id] = _at_site(second.id, second_site)
            available = [item for item in available if item[0] != selected_index]
        return _replace_atoms(exit_layout, replacements)


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
