"""Prepare CZ layouts using explicit retained-state snapshots."""

from math import hypot

from ..device import Device, Position, Region, SLMSite
from ..state import AODHolder, AtomLayout, AtomState, SLMHolder
from .catalog import GatePairCatalog


def reachable_storage_sites(
    device: Device, aod_id: str, storage_zone_id: str, coverage: Region
) -> tuple[SLMSite, ...]:
    """Return distinct reachable storage positions outside the pulse region.

    Args:
        device: Static zones, sites, and transport capability.
        aod_id: Transport device used to reach storage.
        storage_zone_id: Storage-region identifier.
        coverage: CZ pulse region to leave.

    Returns:
        Fixed traps in device order, using one site per physical position.

    Raises:
        ValueError: If the requested storage zone is absent.
        KeyError: If the requested AOD is absent.
    """
    zone = next((zone for zone in device.zones if zone.id == storage_zone_id), None)
    if zone is None:
        raise ValueError(f"Missing storage zone {storage_zone_id!r}.")
    workspace = device.aod(aod_id).workspace
    positions: dict[Position, SLMSite] = {}
    for site in device.slm_sites:
        if (
            zone.bounds.contains(site.position)
            and workspace.contains(site.position)
            and not coverage.contains(site.position)
        ):
            positions.setdefault(site.position, site)
    return tuple(positions.values())


def prepare_gate_layout(
    layout: AtomLayout,
    storage: tuple[SLMSite, ...],
    catalog: GatePairCatalog,
    participants: set[int],
    retained_atoms: tuple[AtomState, ...],
) -> tuple[AtomLayout, frozenset[int]]:
    """Keep valid explicit anchors and clear every other atom from CZ coverage.

    Args:
        layout: Current positions, holders, and physical-qubit binding.
        storage: Reachable fixed storage traps outside pulse coverage.
        catalog: Fixed gate sites and pulse coverage.
        participants: Atom IDs in the current CZ layer.
        retained_atoms: Snapshots explicitly retained by the preceding CZ.

    Returns:
        Prepared layout and valid retained participant IDs. An unchanged
        preparation returns the original layout. The caller emits transport
        to a changed layout before constructing the gate arrangement.

    Raises:
        ValueError: If initially vacant storage cannot accommodate cleared atoms.
    """
    current = {atom.id: atom for atom in layout.atoms}
    gate_sites = {site.id for pair in catalog.pairs for site in pair}
    retained = frozenset(
        atom.id
        for atom in retained_atoms
        if atom.id in participants
        and current.get(atom.id) == atom
        and isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id in gate_sites
    )
    clear = [
        atom
        for atom in layout.atoms
        if atom.id not in retained and catalog.coverage.contains(atom.position)
    ]
    if not clear:
        return layout, retained
    occupied = {atom.position for atom in layout.atoms}
    free = [site for site in storage if site.position not in occupied]
    if len(free) < len(clear):
        raise ValueError("Gate preparation needs more initially vacant storage sites.")
    changes: dict[int, AtomState] = {}
    for atom in clear:
        selected = min(
            free,
            key=lambda site: (
                hypot(
                    site.position.x - atom.position.x,
                    site.position.y - atom.position.y,
                ),
                site.id,
            ),
        )
        changes[atom.id] = AtomState(atom.id, selected.position, SLMHolder(selected.id))
        free.remove(selected)
    atoms = tuple(changes.get(atom.id, atom) for atom in layout.atoms)
    carried = {
        atom.holder.aod_id for atom in atoms if isinstance(atom.holder, AODHolder)
    }
    grids = tuple(grid for grid in layout.aod_grids if grid.aod_id in carried)
    return AtomLayout(atoms, grids, layout.qubit_to_atom), retained
