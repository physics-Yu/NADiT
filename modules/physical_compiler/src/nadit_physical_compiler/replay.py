"""Apply constructed atom actions to positions and trap ownership."""

from .atom_ir import AtomProgram, AtomStep, Load, Measure, Move, Store
from .device import Device
from .state import AODHolder, AtomLayout, AtomState, SLMHolder


def apply_step(layout: AtomLayout, step: AtomStep, device: Device) -> AtomLayout:
    """Apply a parallel step using its common entry state.

    Args:
        layout: Positions, holders, and active lines before the step.
        step: Already constructed parallel actions.
        device: Fixed SLM coordinates used by store actions.

    Returns:
        Updated layout with the same qubit binding and atom tuple order.
        AOD grids are ordered by ID; grids with no active lines are omitted.

    Raises:
        NotImplementedError: For destructive readout; replay currently supports
            retained atoms and preserves their physical-qubit binding.
        KeyError: If an action references an absent atom or SLM site.

    Transport constraints are established by construction, not rechecked here.
    Pulses, reset, retained measurement, and waiting leave this state unchanged.
    """
    entry_atoms = {atom.id: atom for atom in layout.atoms}
    updates: dict[int, AtomState] = {}
    grids = {grid.aod_id: grid for grid in layout.aod_grids}
    for action in step.actions:
        if isinstance(action, Load):
            for atom_id, row, column in action.bindings:
                updates[atom_id] = AtomState(
                    atom_id,
                    entry_atoms[atom_id].position,
                    AODHolder(action.grid.aod_id, row, column),
                )
        elif isinstance(action, Move):
            for atom_id, position in action.targets:
                updates[atom_id] = AtomState(
                    atom_id, position, entry_atoms[atom_id].holder
                )
        elif isinstance(action, Store):
            for atom_id, site_id in action.bindings:
                updates[atom_id] = AtomState(
                    atom_id, device.site(site_id).position, SLMHolder(site_id)
                )
        elif isinstance(action, Measure) and not action.retain_atoms:
            raise NotImplementedError(
                "Replay currently supports retained readout only."
            )
        if isinstance(action, Load | Move | Store):
            grids[action.grid.aod_id] = action.grid
    return AtomLayout(
        tuple(updates.get(atom.id, atom) for atom in layout.atoms),
        tuple(grid for _, grid in sorted(grids.items()) if grid.rows or grid.columns),
        layout.qubit_to_atom,
    )


def replay(program: AtomProgram, entry_state: AtomLayout, device: Device) -> AtomLayout:
    """Return the final layout after applying a program's steps in order.

    Args:
        program: Constructed Atom IR program.
        entry_state: Initial atom positions, holders, and qubit binding.
        device: Static device geometry.

    Returns:
        Final atom layout. An empty program returns the entry state.

    Raises:
        NotImplementedError: If the program contains destructive readout.
        KeyError: If a transport action references an absent atom or SLM site.
    """
    layout = entry_state
    for step in program.steps:
        layout = apply_step(layout, step, device)
    return layout
