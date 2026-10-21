"""Place a global rotation's participants inside its fixed optical coverage."""

from math import hypot

from ..contracts import GateLayer
from ..device import Device, Region, SLMSite
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout, AtomState, SLMHolder


def global_rotation_assignments(
    dag: PhysicalDAG,
    layer: GateLayer,
    layout: AtomLayout,
    device: Device,
    aod_id: str,
) -> dict[int, AtomState]:
    """Construct SLM destinations while preserving already legal atom states.

    Args:
        dag: Native circuit supplying the global rotation's physical qubits.
        layer: Global-rotation operations selected for this execution layer.
        layout: Current atom identities, holders, positions, and qubit binding.
        device: Fixed pulse coverage, allowed holders, zones, and SLM sites.
        aod_id: Transport workspace used to reach destination sites.
    Returns:
        Replacements for misplaced participants and illuminated spectators.
        Assignments follow vacancies, so they introduce no closed atom swaps.
    Raises:
        ValueError: Global control is absent or no supported vacant destination
            can be reached for a remaining atom.
    """
    capability = device.global_control
    if capability is None:
        raise ValueError("Global RY requires a global-control capability.")
    coverage = _coverage(device)
    participants = {
        layout.qubit_to_atom[qubit]
        for operation_id in layer.operation_ids
        for qubit in dag.nodes[operation_id].qubits
    }
    workspace = device.aod(aod_id).workspace
    sites = tuple(
        site for site in device.slm_sites if workspace.contains(site.position)
    )
    inside = tuple(
        site
        for site in sites
        if "slm" in capability.holders
        and coverage.contains(site.position)
        and device.zones_at(site.position, "rotate")
    )
    outside = tuple(site for site in sites if not coverage.contains(site.position))
    pending: dict[int, tuple[SLMSite, ...]] = {}
    for atom in layout.atoms:
        if atom.id in participants:
            holder = "slm" if isinstance(atom.holder, SLMHolder) else "aod"
            legal = (
                holder in capability.holders
                and coverage.contains(atom.position)
                and bool(device.zones_at(atom.position, "rotate"))
            )
            destinations = inside
        else:
            legal = not coverage.contains(atom.position)
            destinations = outside
        if not legal:
            pending[atom.id] = destinations if workspace.contains(atom.position) else ()
    return _assign_vacancies(layout, pending)


def _coverage(device: Device) -> Region:
    capability = device.global_control
    assert capability is not None
    if capability.region is not None:
        return Region(*capability.region)
    bounds = tuple(zone.bounds for zone in device.zones)
    return Region(
        min(region.x_min for region in bounds),
        min(region.y_min for region in bounds),
        max(region.x_max for region in bounds),
        max(region.y_max for region in bounds),
    )


def _assign_vacancies(
    layout: AtomLayout, pending: dict[int, tuple[SLMSite, ...]]
) -> dict[int, AtomState]:
    occupied = {atom.position: atom.id for atom in layout.atoms}
    replacements: dict[int, AtomState] = {}
    while pending:
        for atom_id, destinations in pending.items():
            free = [
                site
                for site in destinations
                if occupied.get(site.position) in (None, atom_id)
            ]
            if not free:
                continue
            atom = layout.atom(atom_id)
            selected = min(
                free,
                key=lambda site: (
                    hypot(
                        atom.position.x - site.position.x,
                        atom.position.y - site.position.y,
                    ),
                    site.id,
                ),
            )
            replacements[atom_id] = AtomState(
                atom_id, selected.position, SLMHolder(selected.id)
            )
            del occupied[atom.position]
            occupied[selected.position] = atom_id
            del pending[atom_id]
            break
        else:
            raise ValueError(
                "Global RY has no vacant reachable SLM destination "
                "for its required addressing."
            )
    return replacements
