"""Construct simultaneous strict batches with occupancy-aware closure."""

from dataclasses import dataclass
from math import hypot

from ..atom_ir import AtomAction, AtomStep, Load, Move, Store
from ..contracts import AtomFragment
from ..device import Device, Position
from ..replay import apply_step
from ..state import AODGrid, AODHolder, AtomLayout, AtomState, SLMHolder
from .occupancy import Occupancy
from .pair import _without_empty_grids
from .rules import MoveRequest, TransportRules, build_conflict_map, rules_for
from .sequential import SequentialTransport


@dataclass(frozen=True, slots=True)
class StrictTransport:
    """Use common LOAD/MOVE/STORE phases for compatible SLM movements.

    Args:
        aod_id: Transport device used by every constructed batch.
    """

    aod_id: str

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Select strict batches and update occupancy after each transfer.

        Args:
            layout: Current atom positions, holders, and physical-qubit binding.
            target_layout: Requested endpoints for the same atoms.
            device: Fixed traps, AOD motion capabilities, and transport timing.

        Returns:
            Atom IR steps and their resulting layout. Singleton AOD readout
            boundaries use the existing sequential transport constructor.

        Raises:
            ValueError: For inconsistent endpoints, unreachable positions,
                unsupported AOD boundaries, or dependencies requiring a buffer.
            KeyError: If an AOD or referenced SLM site is absent.
        """
        current = _without_empty_grids(layout)
        target = _without_empty_grids(target_layout)
        if (
            current.aod_grids
            or target.aod_grids
            or any(
                isinstance(atom.holder, AODHolder)
                for boundary in (current, target)
                for atom in boundary.atoms
            )
        ):
            return SequentialTransport(self.aod_id).construct(current, target, device)
        rules = rules_for(device.aod(self.aod_id), device.timing)
        sources = {atom.id: atom for atom in current.atoms}
        destinations = {atom.id: atom for atom in target.atoms}
        moves = _requests(current, target, sources, destinations, device, rules)
        occupied = Occupancy({atom.id: atom.position for atom in current.atoms})
        selector = _BatchSelector(
            rules,
            {move.atom_id: move for move in moves},
            build_conflict_map(moves, rules),
            occupied,
        )
        pending = {move.atom_id for move in moves}
        steps: list[AtomStep] = []
        while pending:
            batch = selector.select(pending)
            fragment = _construct_batch(batch, current, destinations, device, rules)
            steps.extend(fragment.steps)
            current = fragment.exit_layout
            for move in batch:
                occupied.remove(move.atom_id)
                pending.remove(move.atom_id)
            for move in batch:
                occupied.add(move.atom_id, destinations[move.atom_id].position)
        return AtomFragment(tuple(steps), current)


@dataclass
class _BatchSelector:
    rules: TransportRules
    requests: dict[int, MoveRequest]
    conflicts: dict[int, frozenset[int]]
    occupied: Occupancy

    def select(self, pending: set[int]) -> tuple[MoveRequest, ...]:
        for seed in self.requests:
            if seed not in pending:
                continue
            selected = self._close({seed}, pending)
            if selected is None:
                continue
            for candidate in self.requests:
                if candidate not in pending or candidate in selected:
                    continue
                if self.conflicts[candidate] & selected:
                    continue
                enlarged = self._close(selected | {candidate}, pending)
                if enlarged is not None:
                    selected = enlarged
            return tuple(
                move for atom_id, move in self.requests.items() if atom_id in selected
            )
        raise ValueError(
            "No strict batch can proceed; closed dependencies need a buffer."
        )

    def _close(self, selected: set[int], pending: set[int]) -> set[int] | None:
        selected = set(selected)
        while True:
            moves = tuple(
                move for atom_id, move in self.requests.items() if atom_id in selected
            )
            if not self.rules.can_fit(moves):
                return None
            source, target, _ = self.rules.frames(moves)
            blockers: set[int] = set()
            for grid in (source, target):
                for row in grid.rows:
                    for column in grid.columns:
                        blockers.update(
                            self.occupied.at(
                                Position(column.coordinate, row.coordinate)
                            )
                        )
            blockers -= selected
            if not blockers:
                return selected
            if not blockers <= pending:
                return None
            for atom_id in self.requests:
                if atom_id not in blockers:
                    continue
                if self.conflicts[atom_id] & selected:
                    return None
                selected.add(atom_id)


def _requests(
    current: AtomLayout,
    target: AtomLayout,
    sources: dict[int, AtomState],
    destinations: dict[int, AtomState],
    device: Device,
    rules: TransportRules,
) -> tuple[MoveRequest, ...]:
    if sources.keys() != destinations.keys():
        raise ValueError("Transport must retain the same atom identities.")
    if current.qubit_to_atom != target.qubit_to_atom:
        raise ValueError("Transport must retain the physical-qubit mapping.")
    moves: list[MoveRequest] = []
    for atom_id, before in sources.items():
        after = destinations[atom_id]
        if before == after:
            continue
        for atom in (before, after):
            assert isinstance(atom.holder, SLMHolder)
            if device.site(atom.holder.site_id).position != atom.position:
                raise ValueError(
                    f"Atom {atom.id} position does not match its SLM holder."
                )
        move = MoveRequest(atom_id, before.position, after.position)
        if not rules.can_fit((move,)):
            raise ValueError(f"Atom {atom_id} endpoints are outside the AOD workspace.")
        moves.append(move)
    return tuple(moves)


def _construct_batch(
    moves: tuple[MoveRequest, ...],
    layout: AtomLayout,
    destinations: dict[int, AtomState],
    device: Device,
    rules: TransportRules,
) -> AtomFragment:
    source, target, bindings = rules.frames(moves)
    actions: list[AtomAction] = [
        Load(source, bindings, duration_us=device.timing.load_us)
    ]
    if any(move.source != move.target for move in moves):
        distance = max(
            hypot(move.target.x - move.source.x, move.target.y - move.source.y)
            for move in moves
        )
        actions.append(
            Move(
                target,
                tuple((move.atom_id, move.target) for move in moves),
                duration_us=device.timing.move_duration(distance),
            )
        )
    stores: list[tuple[int, str]] = []
    for move in moves:
        holder = destinations[move.atom_id].holder
        assert isinstance(holder, SLMHolder)
        stores.append((move.atom_id, holder.site_id))
    actions.append(
        Store(AODGrid(rules.aod.id), tuple(stores), duration_us=device.timing.store_us)
    )
    steps = tuple(AtomStep((action,)) for action in actions)
    for step in steps:
        layout = apply_step(layout, step, device)
    return AtomFragment(steps, layout)
