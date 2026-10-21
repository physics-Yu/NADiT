"""Emit closed AOD readout trips while preserving measurement identities."""

from dataclasses import dataclass
from math import hypot

from ..atom_ir import AtomAction, AtomStep, Load, Measure, Move, Store
from ..contracts import AtomFragment, GateLayer
from ..device import Device
from ..physical_ir import PhysicalDAG
from ..state import AODGrid, AtomLayout, AtomState, SLMHolder
from ..transport.rules import rules_for
from .planning import ReadoutBatch, plan_readout


@dataclass(frozen=True, slots=True)
class BatchReadout:
    """Measure flexible AOD batches and return every atom to its original site."""

    aod_id: str

    def lower(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> AtomFragment:
        """Construct readout actions without advancing the DAG or sampling results.

        Inputs are an already-ready measurement layer and its SLM entry layout.
        Planning raises ValueError for unsupported devices or unreachable readout.
        Returned completed IDs and ResultRefs refer to the original physical DAG.
        """
        batches = plan_readout(dag, layer, layout, device, self.aod_id)
        steps = tuple(
            step
            for batch in batches
            for step in _emit(batch, dag, layout, device, self.aod_id)
        )
        return AtomFragment(steps, layout, layer.operation_ids, retained_atoms)


def _emit(
    batch: ReadoutBatch,
    dag: PhysicalDAG,
    layout: AtomLayout,
    device: Device,
    aod_id: str,
) -> tuple[AtomStep, ...]:
    assert device.readout is not None
    rules = rules_for(device.aod(aod_id), device.timing)
    source, target, bindings = rules.frames(batch.moves)
    actions: list[AtomAction] = [
        Load(source, bindings, duration_us=device.timing.load_us)
    ]
    distance = max(
        hypot(m.target.x - m.source.x, m.target.y - m.source.y) for m in batch.moves
    )
    if distance:
        actions.append(
            Move(
                target,
                tuple((m.atom_id, m.target) for m in batch.moves),
                duration_us=device.timing.move_duration(distance),
            )
        )
    results = []
    for move, index in zip(batch.moves, batch.operation_ids, strict=True):
        result = dag.nodes[index].result
        assert result is not None
        results.append((move.atom_id, result))
    actions.append(
        Measure(
            device.readout.channel,
            tuple(results),
            True,
            duration_us=device.readout.duration_us,
            source_ids=batch.operation_ids,
        )
    )
    if distance:
        actions.append(
            Move(
                source,
                tuple((m.atom_id, m.source) for m in batch.moves),
                duration_us=device.timing.move_duration(distance),
            )
        )
    stores = []
    for move in batch.moves:
        holder = layout.atom(move.atom_id).holder
        assert isinstance(holder, SLMHolder)
        stores.append((move.atom_id, holder.site_id))
    actions.append(
        Store(AODGrid(aod_id), tuple(stores), duration_us=device.timing.store_us)
    )
    return tuple(AtomStep((action,)) for action in actions)
