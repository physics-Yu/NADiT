"""Automatic initial placement adapted from MQT QMAP HeuristicPlacer.cpp."""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from ..state import AtomLayout, AtomState, SLMHolder
from .geometry import PlacementGeometry


def make_initial_layout(
    n_qubits: int,
    geometry: PlacementGeometry,
    *,
    atom_ids: tuple[int, ...] | None = None,
) -> AtomLayout:
    """Fill storage arrays row by row, starting on the side toward the gate zone.

    Args:
        n_qubits: Physical qubits to bind.
        geometry: Storage declaration order and the first entanglement array.
        atom_ids: Optional stable atom IDs in physical-qubit order.

    Returns:
        An SLM-held layout with one atom per physical qubit.

    Raises:
        ValueError: If the qubit count is negative, storage capacity is
            insufficient, or the optional atom ID count differs.

    The first storage/gate origins determine one direction for all arrays,
    matching the source constructor. Each new SLM restarts at that direction's
    first row; this corrects the source's reversed reset and end dereference.
    """
    ids = tuple(range(n_qubits)) if atom_ids is None else atom_ids
    if n_qubits < 0 or len(ids) != n_qubits:
        raise ValueError("Initial atom IDs must match a nonnegative qubit count.")
    capacity = sum(array.n_rows * array.n_cols for array in geometry.storage_arrays)
    if n_qubits > capacity:
        raise ValueError(
            f"Initial placement needs {n_qubits} storage sites; {capacity} available."
        )
    if n_qubits == 0:
        return AtomLayout(())
    reverse = (
        geometry.storage_arrays[0].origin.y
        < geometry.entanglement_arrays[0][0].origin.y
    )
    ordered = (
        site
        for array in geometry.storage_arrays
        for row in (reversed(array.sites) if reverse else array.sites)
        for site in row
    )
    atoms = tuple(
        AtomState(atom_id, site.position, SLMHolder(site.id))
        for atom_id, site in zip(ids, ordered, strict=False)
    )
    return AtomLayout(atoms, qubit_to_atom=ids)
