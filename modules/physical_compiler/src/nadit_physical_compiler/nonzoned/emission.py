"""Emit selected batches without repartitioning or replay in the compiler."""

from ..atom_ir import AtomStep, Load, Move, Store
from ..device import Device
from ..state import AODGrid, AtomLayout, SLMHolder
from ..transport.rules import MoveRequest, rules_for
from .planning import Candidate


def emit_batch(
    batch: tuple[Candidate, ...],
    reference: AtomLayout,
    device: Device,
    aod_id: str,
    *,
    returning: bool = False,
) -> tuple[AtomStep, ...]:
    """Lower one accepted strict batch, or its exact inverse, into Atom IR.

    Each batch starts and ends in SLM traps with an empty AOD. Returning batches
    are consumed in reverse order so the original occupancy admissions hold.
    """
    rules = rules_for(device.aod(aod_id), device.timing)
    moves = tuple(
        MoveRequest(c.move.atom_id, c.move.target, c.move.source)
        if returning
        else c.move
        for c in batch
    )
    source, target, bindings = rules.frames(moves)
    steps = [AtomStep((Load(source, bindings, duration_us=device.timing.load_us),))]
    distance_time = device.timing.move_duration(
        max(
            ((m.target.x - m.source.x) ** 2 + (m.target.y - m.source.y) ** 2) ** 0.5
            for m in moves
        )
    )
    if any(m.source != m.target for m in moves):
        steps.append(
            AtomStep(
                (
                    Move(
                        target,
                        tuple((m.atom_id, m.target) for m in moves),
                        duration_us=distance_time,
                    ),
                )
            )
        )
    stores = []
    for candidate in batch:
        atom_id = candidate.move.atom_id
        home = reference.atom(atom_id).holder
        assert isinstance(home, SLMHolder)
        stores.append(
            (atom_id, home.site_id if returning else candidate.destination.id)
        )
    steps.append(
        AtomStep(
            (Store(AODGrid(aod_id), tuple(stores), duration_us=device.timing.store_us),)
        )
    )
    return tuple(steps)
