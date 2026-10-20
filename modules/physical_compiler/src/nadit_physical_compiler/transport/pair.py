"""Direct transport construction for one pair and singleton AOD readout."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from math import hypot

from ..atom_ir import AtomAction, AtomStep, Load, Move, Store
from ..contracts import AtomFragment
from ..device import AOD, Device
from ..replay import apply_step
from ..state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
)


@dataclass(frozen=True, slots=True)
class PairTransport:
    """Use a single flexible AOD for at most two atoms.

    Args:
        aod_id: Transport AOD in the device configuration.
    """

    aod_id: str

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Construct grouped or sequential transfers between selected holders.

        Args:
            layout: Current layout with at most two atoms.
            target_layout: Same atoms and wire mapping at selected destinations.
            device: Fixed sites, flexible AOD capabilities, and transport timing.

        Returns:
            Atom steps and their replayed exit layout, omitting empty grids.

        Raises:
            ValueError: For a destination outside the workspace, a closed swap,
                or a layout outside the pair and singleton-readout profile.
            KeyError: If the selected AOD is absent from the device.
        """
        aod = device.aod(self.aod_id)
        current = _without_empty_grids(layout)
        target = _without_empty_grids(target_layout)
        _require_profile(current, target, aod)
        destinations = {atom.id: atom for atom in target.atoms}
        movers = [atom for atom in current.atoms if atom != destinations[atom.id]]
        if not movers:
            return AtomFragment((), current)
        steps: list[AtomStep] = []

        def emit(action: AtomAction) -> None:
            nonlocal current
            step = AtomStep((action,))
            steps.append(step)
            current = apply_step(current, step, device)

        if _can_group(movers, destinations, aod):
            ordered = sorted(movers, key=lambda atom: atom.position.x)
            after = [destinations[atom.id] for atom in ordered]
            _transfer_pair(ordered, after, aod.id, device, emit)
        else:
            pending = {atom.id for atom in movers}
            while pending:
                for atom in current.atoms:
                    if atom.id not in pending:
                        continue
                    destination = destinations[atom.id]
                    if any(
                        other.id != atom.id and other.position == destination.position
                        for other in current.atoms
                    ):
                        continue
                    _transfer_one(atom, destination, aod.id, device, emit)
                    pending.remove(atom.id)
                    break
                else:
                    raise ValueError("A closed atom swap requires a temporary buffer.")
        return AtomFragment(tuple(steps), current)


def _without_empty_grids(layout: AtomLayout) -> AtomLayout:
    return replace(
        layout,
        aod_grids=tuple(grid for grid in layout.aod_grids if grid.rows or grid.columns),
    )


def _require_profile(source: AtomLayout, target: AtomLayout, aod: AOD) -> None:
    if len(source.atoms) > 2 or len(target.atoms) > 2:
        raise ValueError("PairTransport supports at most two atoms.")
    if {atom.id for atom in source.atoms} != {atom.id for atom in target.atoms}:
        raise ValueError("Transport must retain the same atom identities.")
    if source.qubit_to_atom != target.qubit_to_atom:
        raise ValueError("Transport must retain the physical-qubit mapping.")
    carried_ids: set[int] = set()
    for layout in (source, target):
        carried = [atom for atom in layout.atoms if isinstance(atom.holder, AODHolder)]
        if len(carried) > 1:
            raise ValueError("AOD fragment boundaries support singleton readout only.")
        for atom in layout.atoms:
            if not aod.workspace.contains(atom.position):
                raise ValueError(f"Atom {atom.id} is outside the AOD workspace.")
        if carried:
            atom = carried[0]
            holder = atom.holder
            assert isinstance(holder, AODHolder)
            expected = _singleton_grid(atom, holder)
            if holder.aod_id != aod.id or layout.aod_grids != (expected,):
                raise ValueError(
                    "Singleton AOD boundaries must use only their bound lines."
                )
            carried_ids.add(atom.id)
        elif layout.aod_grids:
            raise ValueError("Parked AOD lines are outside the pair transport profile.")
    if carried_ids:
        changed = {atom.id for atom in source.atoms if atom != target.atom(atom.id)}
        if len(carried_ids) != 1 or changed - carried_ids:
            raise ValueError("AOD readout transport must move only its singleton atom.")


def _can_group(
    movers: list[AtomState], destinations: dict[int, AtomState], aod: AOD
) -> bool:
    if len(movers) != 2 or (aod.max_columns is not None and aod.max_columns < 2):
        return False
    before = sorted(movers, key=lambda atom: atom.position.x)
    after = [destinations[atom.id] for atom in before]
    return (
        all(isinstance(atom.holder, SLMHolder) for atom in (*before, *after))
        and before[0].position.y == before[1].position.y
        and after[0].position.y == after[1].position.y
        and before[1].position.x - before[0].position.x >= aod.min_spacing
        and after[1].position.x - after[0].position.x >= aod.min_spacing
    )


def _singleton_grid(atom: AtomState, holder: AODHolder) -> AODGrid:
    return AODGrid(
        holder.aod_id,
        (AODLine(holder.row, atom.position.y),),
        (AODLine(holder.column, atom.position.x),),
    )


def _row_grid(atoms: list[AtomState], aod_id: str) -> AODGrid:
    return AODGrid(
        aod_id,
        (AODLine(0, atoms[0].position.y),),
        tuple(AODLine(column, atom.position.x) for column, atom in enumerate(atoms)),
    )


def _distance(before: AtomState, after: AtomState) -> float:
    return hypot(
        after.position.x - before.position.x,
        after.position.y - before.position.y,
    )


def _transfer_pair(
    before: list[AtomState],
    after: list[AtomState],
    aod_id: str,
    device: Device,
    emit: Callable[[AtomAction], None],
) -> None:
    emit(
        Load(
            _row_grid(before, aod_id),
            tuple((atom.id, 0, column) for column, atom in enumerate(before)),
            duration_us=device.timing.load_us,
        )
    )
    distance = max(_distance(old, new) for old, new in zip(before, after, strict=True))
    if distance:
        emit(
            Move(
                _row_grid(after, aod_id),
                tuple((atom.id, atom.position) for atom in after),
                duration_us=device.timing.move_duration(distance),
            )
        )
    bindings = []
    for atom in after:
        assert isinstance(atom.holder, SLMHolder)
        bindings.append((atom.id, atom.holder.site_id))
    emit(Store(AODGrid(aod_id), tuple(bindings), duration_us=device.timing.store_us))


def _transfer_one(
    before: AtomState,
    after: AtomState,
    aod_id: str,
    device: Device,
    emit: Callable[[AtomAction], None],
) -> None:
    if isinstance(before.holder, AODHolder):
        holder = before.holder
        if isinstance(after.holder, AODHolder) and after.holder != holder:
            raise ValueError("AOD movement must retain singleton line identities.")
    else:
        holder = (
            after.holder
            if isinstance(after.holder, AODHolder)
            else AODHolder(aod_id, 0, 0)
        )
        emit(
            Load(
                _singleton_grid(before, holder),
                ((before.id, holder.row, holder.column),),
                duration_us=device.timing.load_us,
            )
        )
    if before.position != after.position:
        emit(
            Move(
                _singleton_grid(after, holder),
                ((after.id, after.position),),
                duration_us=device.timing.move_duration(_distance(before, after)),
            )
        )
    if isinstance(after.holder, SLMHolder):
        emit(
            Store(
                AODGrid(aod_id),
                ((after.id, after.holder.site_id),),
                duration_us=device.timing.store_us,
            )
        )
