"""Construct the full-array discretization context used by placement scores."""

from ..device import SLMSite
from ..state import AtomState
from .geometry import PlacementGeometry
from .scoring import PlacementCoordinates, PlacementJob


def build_coordinates(
    atoms: tuple[AtomState, ...],
    sites: tuple[SLMSite, ...],
    geometry: PlacementGeometry,
    jobs: tuple[PlacementJob, ...],
) -> PlacementCoordinates:
    """Keep score ranks independent of the selected candidate windows.

    Args:
        atoms: All atoms in the gate or storage search jobs.
        sites: All free target sites, before applying candidate windows.
        geometry: Ordered SLM identities and lattice indices.
        jobs: Generated jobs; storage jobs include the geometric nearest site.

    Returns:
        Source/free-target coordinates and nearest storage column coordinates.
        Gate jobs have no nearest-storage column metadata.

    A full nearest column uses the closest free column in the same SLM. This
    corrects the source fallback's boolean assignment to its distance variable.
    """
    free_columns: dict[str, dict[int, float]] = {}
    for site in sites:
        array, _, column = geometry.locate(site.id)
        free_columns.setdefault(array.id, {})[column] = site.position.x
    nearest_columns: list[float] = []
    for job in jobs:
        if job.nearest_site is None:
            continue
        array, _, nearest = geometry.locate(job.nearest_site.id)
        columns = free_columns.get(array.id, {})
        if columns:
            column = min(columns, key=lambda item: (abs(item - nearest), item))
            nearest_columns.append(columns[column])
        elif sites:
            # The source initializes this rank to zero when that SLM has no
            # free column. A stay option may still complete this storage job.
            nearest_columns.append(min(site.position.x for site in sites))
    return PlacementCoordinates(
        tuple(atom.position for atom in atoms), sites, tuple(nearest_columns)
    )
