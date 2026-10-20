"""Compose source-aligned gate and exit placement searches over fixed sites.

Reference: MQT QMAP cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d,
HeuristicPlacer.cpp (MIT); see the repository third-party notice.
"""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from dataclasses import dataclass
from math import hypot

from ..contracts import GateLayer, PlacementPlan
from ..device import Device, Position, SLMSite
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout, AtomState
from ..transport.rules import StrictRules
from .candidates import gate_jobs, storage_jobs
from .catalog import GatePairCatalog
from .multipair import _anchor_pair, _at_site, _replace_atoms
from .pair import PairPlacer
from .scoring import PlacementConfig, search_placement


@dataclass(frozen=True, slots=True)
class HeuristicPlacer:
    """Search gate assignments and optional retention with shared transport rules.

    Args:
        aod_id: Flexible AOD used to reach selected sites.
        catalog: Validated, isolated gate pairs and their pulse coverage.
        storage_zone_id: Region supplying fixed storage traps.
        config: Bounded IDS and source-aligned proxy-score parameters.
    """

    aod_id: str
    catalog: GatePairCatalog
    storage_zone_id: str = "storage"
    config: PlacementConfig = PlacementConfig()

    def place(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        reuse_qubits: tuple[int, ...] = (),
        following: GateLayer | None = None,
    ) -> PlacementPlan:
        """Search one CZ execution layout and its storage/stay exit layout.

        Args:
            dag: Native operations and stable qubit identities.
            layer: Disjoint homogeneous operations selected for this layer.
            layout: Current positions, holders, and qubit-to-atom mapping.
            device: Fixed traps, transport constraints, and operation capabilities.
            reuse_qubits: Matched current qubits eligible for optional retention.
            following: Known next CZ layer supplying next-partner positions.

        Returns:
            Selected layouts with actual retention encoded by exit holders.
            Local operations and readout use the existing placement helper.

        Raises:
            ValueError: If capacity, safe spectator storage, compatible anchors,
                or a complete gate/storage assignment is unavailable.
        """
        if not layer.operation_ids or dag.nodes[layer.operation_ids[0]].name != "cz":
            return PairPlacer(self.aod_id).place(
                dag, layer, layout, device, reuse_qubits=reuse_qubits
            )
        participants = tuple(
            layout.qubit_to_atom[q]
            for operation_id in layer.operation_ids
            for q in dag.nodes[operation_id].qubits
        )
        participant_ids = set(participants)
        entry_partners = _partner_positions(dag, following, layout)
        reusable = frozenset(
            layout.qubit_to_atom[q]
            for q in reuse_qubits
            if layout.qubit_to_atom[q] in participant_ids
            and layout.qubit_to_atom[q] in entry_partners
        )
        storage = self._storage_sites(device)
        cleared = self._clear_spectators(layout, storage, participant_ids)
        rules = StrictRules(device.aod(self.aod_id), device.timing)
        execution = self._execution(
            dag, layer, cleared, rules, reusable, entry_partners
        )
        reserved = {
            atom.position for atom in execution.atoms if atom.id not in participant_ids
        }
        free_storage = tuple(site for site in storage if site.position not in reserved)
        execution_atoms = {atom.id: atom for atom in execution.atoms}
        jobs = storage_jobs(
            tuple(execution_atoms[atom_id] for atom_id in participants),
            free_storage,
            reuse_atoms=reusable,
            partner_positions=_partner_positions(dag, following, execution),
            config=self.config,
        )
        selected = search_placement(jobs, rules, self.config, stage="storage")
        exit_layout = _replace_atoms(
            execution,
            {
                atom_id: _at_site(atom_id, site)
                for option in selected
                for atom_id, site in option.assignments
            },
        )
        return PlacementPlan(execution, exit_layout)

    def _execution(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        rules: StrictRules,
        reusable: frozenset[int],
        partners: dict[int, Position],
    ) -> AtomLayout:
        available = {
            index: pair
            for index, pair in enumerate(self.catalog.pairs)
            if all(rules.aod.workspace.contains(site.position) for site in pair)
        }
        if len(layer.operation_ids) > len(available):
            raise ValueError("CZ layer exceeds reachable gate-pair capacity.")
        locations = {
            site.id: (index, side)
            for index, pair in enumerate(self.catalog.pairs)
            for side, site in enumerate(pair)
        }
        assigned: dict[int, AtomState] = {}
        unanchored: list[tuple[AtomState, AtomState]] = []
        atoms = {atom.id: atom for atom in layout.atoms}
        for operation_id in layer.operation_ids:
            first, second = (
                atoms[layout.qubit_to_atom[q]] for q in dag.nodes[operation_id].qubits
            )
            anchor = _anchor_pair(first, second, locations, self.catalog.pairs)
            if anchor is None:
                unanchored.append((first, second))
                continue
            index, first_site, second_site = anchor
            if index not in available:
                raise ValueError(
                    "Gate anchors require the same or an unreachable pair."
                )
            del available[index]
            assigned[first.id] = _at_site(first.id, first_site)
            assigned[second.id] = _at_site(second.id, second_site)
        jobs = gate_jobs(
            tuple(unanchored),
            tuple(available.values()),
            reuse_atoms=reusable,
            partner_positions=partners,
            config=self.config,
        )
        selected = search_placement(jobs, rules, self.config, stage="gate")
        assigned.update(
            (atom_id, _at_site(atom_id, site))
            for option in selected
            for atom_id, site in option.assignments
        )
        return _replace_atoms(layout, assigned)

    def _storage_sites(self, device: Device) -> tuple[SLMSite, ...]:
        zone = next(
            (zone for zone in device.zones if zone.id == self.storage_zone_id), None
        )
        if zone is None:
            raise ValueError(f"Missing storage zone {self.storage_zone_id!r}.")
        workspace = device.aod(self.aod_id).workspace
        positions: dict[Position, SLMSite] = {}
        for site in device.slm_sites:
            if (
                zone.bounds.contains(site.position)
                and workspace.contains(site.position)
                and not self.catalog.coverage.contains(site.position)
            ):
                positions.setdefault(site.position, site)
        return tuple(positions.values())

    def _clear_spectators(
        self, layout: AtomLayout, sites: tuple[SLMSite, ...], participants: set[int]
    ) -> AtomLayout:
        occupied = {atom.position for atom in layout.atoms}
        changes: dict[int, AtomState] = {}
        for atom in layout.atoms:
            if atom.id in participants or not self.catalog.coverage.contains(
                atom.position
            ):
                continue
            candidates = [site for site in sites if site.position not in occupied]
            if not candidates:
                raise ValueError("A pulse spectator needs initially vacant storage.")
            selected = min(
                candidates,
                key=lambda site: (
                    hypot(
                        site.position.x - atom.position.x,
                        site.position.y - atom.position.y,
                    ),
                    site.id,
                ),
            )
            changes[atom.id] = _at_site(atom.id, selected)
            occupied.add(selected.position)
        return _replace_atoms(layout, changes) if changes else layout


def _partner_positions(
    dag: PhysicalDAG, following: GateLayer | None, layout: AtomLayout
) -> dict[int, Position]:
    if following is None:
        return {}
    positions = {atom.id: atom.position for atom in layout.atoms}
    partners: dict[int, Position] = {}
    for operation_id in following.operation_ids:
        first, second = (
            layout.qubit_to_atom[q] for q in dag.nodes[operation_id].qubits
        )
        partners[first] = positions[second]
        partners[second] = positions[first]
    return partners
