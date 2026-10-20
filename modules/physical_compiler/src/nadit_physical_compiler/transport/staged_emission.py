"""Translate constructed staging events to explicit AOD-grid Atom IR."""

from math import hypot

from ..atom_ir import AtomAction, AtomStep, Load, Move, Store
from ..contracts import AtomFragment
from ..device import Device, Position
from ..state import AODGrid, AODHolder, AODLine, AtomLayout, AtomState, SLMHolder
from .rules import MoveRequest, StrictRules, _partition
from .staging import StageEvent


def emit_stages(
    events: tuple[StageEvent, ...],
    moves: tuple[MoveRequest, ...],
    layout: AtomLayout,
    destinations: dict[int, AtomState],
    device: Device,
    rules: StrictRules,
) -> AtomFragment:
    """Emit admitted staging events without replay or full-state checking.

    Args:
        events: Constructed transfers and collective position changes.
        moves: Equality-preserving requests, defining stable row/column identities.
        layout: SLM-held entry layout.
        destinations: Final atoms with receiving SLM holders.
        device: Transfer and movement timing.
        rules: AOD geometry used to bind source lines.

    Returns:
        Existing Atom IR actions and the directly constructed exit layout.
    """
    # Numeric IDs label source lines. The staged constructor may insert those
    # lines in another spatial order; IDs stay fixed throughout their lifetime.
    _, rows = _partition(tuple(move.source.y for move in moves))
    _, columns = _partition(tuple(move.source.x for move in moves))
    holders = {
        move.atom_id: AODHolder(rules.aod.id, row, column)
        for move, row, column in zip(moves, rows, columns, strict=True)
    }
    carried: dict[int, AtomState] = {}
    updated: dict[int, AtomState] = {}
    steps: list[AtomStep] = []
    for event in events:
        action: AtomAction
        if event.kind == "load":
            for atom_id, position in event.positions:
                carried[atom_id] = AtomState(atom_id, position, holders[atom_id])
            action = Load(
                _grid(rules.aod.id, carried),
                tuple(
                    (atom_id, holders[atom_id].row, holders[atom_id].column)
                    for atom_id, _ in event.positions
                ),
                duration_us=device.timing.load_us,
            )
        elif event.kind == "move":
            distance = max(
                _distance(carried[atom_id].position, position)
                for atom_id, position in event.positions
            )
            for atom_id, position in event.positions:
                carried[atom_id] = AtomState(atom_id, position, holders[atom_id])
            action = Move(
                _grid(rules.aod.id, carried),
                event.positions,
                duration_us=device.timing.move_duration(distance),
            )
        else:
            stores: list[tuple[int, str]] = []
            for atom_id, _ in event.positions:
                atom = destinations[atom_id]
                assert isinstance(atom.holder, SLMHolder)
                stores.append((atom_id, atom.holder.site_id))
                updated[atom_id] = atom
                del carried[atom_id]
            action = Store(
                _grid(rules.aod.id, carried),
                tuple(stores),
                duration_us=device.timing.store_us,
            )
        steps.append(AtomStep((action,)))
    return AtomFragment(
        tuple(steps),
        AtomLayout(
            tuple(updated.get(atom.id, atom) for atom in layout.atoms),
            tuple(grid for grid in layout.aod_grids if grid.aod_id != rules.aod.id),
            layout.qubit_to_atom,
        ),
    )


def _grid(aod_id: str, carried: dict[int, AtomState]) -> AODGrid:
    rows: dict[int, float] = {}
    columns: dict[int, float] = {}
    for atom in carried.values():
        holder = atom.holder
        assert isinstance(holder, AODHolder)
        rows[holder.row] = atom.position.y
        columns[holder.column] = atom.position.x
    return AODGrid(
        aod_id,
        tuple(AODLine(index, value) for index, value in sorted(rows.items())),
        tuple(AODLine(index, value) for index, value in sorted(columns.items())),
    )


def _distance(first: Position, second: Position) -> float:
    return hypot(first.x - second.x, first.y - second.y)
