"""Singleton transport for arbitrary SLM layouts and retained AOD readout."""

from collections import deque
from dataclasses import dataclass

from ..atom_ir import AtomAction, AtomStep
from ..contracts import AtomFragment
from ..device import Device
from ..replay import apply_step
from ..state import AODHolder, AtomLayout, SLMHolder
from .pair import _singleton_grid, _transfer_one, _without_empty_grids


@dataclass(frozen=True, slots=True)
class SequentialTransport:
    """Transport one atom at a time with a single active row and column.

    Args:
        aod_id: Transport AOD in the device configuration.
    """

    aod_id: str

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Construct transfers in vacancy order and replay their state updates.

        Args:
            layout: Current atom positions, holders, and physical-qubit binding.
            target_layout: Requested positions and holders for the same atoms.
            device: Fixed traps, AOD workspace, and transport timing.

        Returns:
            Singleton Atom IR steps and the resulting layout with empty AOD
            grids omitted. Transport does not complete circuit operations.

        Raises:
            ValueError: For an inconsistent target, an unsupported parked AOD
                group, an unreachable endpoint, or a swap requiring a buffer.
            KeyError: If a requested AOD or SLM site is absent.
        """
        aod = device.aod(self.aod_id)
        current = _without_empty_grids(layout)
        target = _without_empty_grids(target_layout)
        sources = {atom.id: atom for atom in current.atoms}
        destinations = {atom.id: atom for atom in target.atoms}
        if sources.keys() != destinations.keys():
            raise ValueError("Transport must retain the same atom identities.")
        if current.qubit_to_atom != target.qubit_to_atom:
            raise ValueError("Transport must retain the physical-qubit mapping.")
        pending = {
            atom_id
            for atom_id, atom in sources.items()
            if atom != destinations[atom_id]
        }
        carried = {
            atom_id
            for state in (current, target)
            if (atom_id := _readout_atom(state, self.aod_id)) is not None
        }
        if len(carried) > 1 or (carried and pending - carried):
            raise ValueError("AOD readout transport must move only its singleton atom.")
        occupied = {atom.position: atom.id for atom in current.atoms}
        target_positions = {atom.position: atom.id for atom in target.atoms}
        if len(occupied) != len(sources) or len(target_positions) != len(destinations):
            raise ValueError(
                "Each atom must have a distinct source and target position."
            )
        for atom_id in pending:
            for atom in (sources[atom_id], destinations[atom_id]):
                if not aod.workspace.contains(atom.position):
                    raise ValueError(f"Atom {atom.id} is outside the AOD workspace.")
                if (
                    isinstance(atom.holder, SLMHolder)
                    and device.site(atom.holder.site_id).position != atom.position
                ):
                    raise ValueError(
                        f"Atom {atom.id} position does not match its SLM holder."
                    )
        ready = deque(
            atom_id
            for atom_id in sources
            if atom_id in pending
            and occupied.get(destinations[atom_id].position) in (None, atom_id)
        )
        steps: list[AtomStep] = []

        def emit(action: AtomAction) -> None:
            nonlocal current
            step = AtomStep((action,))
            steps.append(step)
            current = apply_step(current, step, device)

        while ready:
            atom_id = ready.popleft()
            before, after = sources[atom_id], destinations[atom_id]
            _transfer_one(before, after, aod.id, device, emit)
            pending.remove(atom_id)
            del occupied[before.position]
            occupied[after.position] = atom_id
            waiting = target_positions.get(before.position)
            if waiting in pending and before.position != after.position:
                assert waiting is not None
                ready.append(waiting)
        if pending:
            raise ValueError("A closed atom swap requires a temporary buffer.")
        return AtomFragment(tuple(steps), current)


def _readout_atom(layout: AtomLayout, aod_id: str) -> int | None:
    carried = [atom for atom in layout.atoms if isinstance(atom.holder, AODHolder)]
    if not carried:
        if layout.aod_grids:
            raise ValueError("Parked AOD lines are outside singleton transport.")
        return None
    if len(carried) != 1:
        raise ValueError("AOD fragment boundaries support singleton readout only.")
    atom = carried[0]
    holder = atom.holder
    assert isinstance(holder, AODHolder)
    if holder.aod_id != aod_id or layout.aod_grids != (_singleton_grid(atom, holder),):
        raise ValueError("Singleton AOD boundaries must use only their bound lines.")
    return atom.id
