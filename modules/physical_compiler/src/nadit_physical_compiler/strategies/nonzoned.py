"""Shared-array CZ layers returning to their explicit reference layout."""

from dataclasses import dataclass

from ..contracts import AtomFragment, GateLayer
from ..device import Device
from ..emission import WholeLayerEmitter, emit_layer
from ..nonzoned.emission import emit_batch
from ..nonzoned.planning import plan_layer
from ..nonzoned.support import require_capabilities
from ..physical_ir import PhysicalDAG
from ..placement import GatePairCatalog
from ..readout import BatchReadout
from ..scheduling import ReadyScheduler
from ..state import AtomLayout, AtomState


@dataclass(frozen=True, slots=True)
class NonZonedStrategy:
    """Compile with fixed homes, ENOLA-inspired batching, and one CZ per layer.

    Args:
        aod_id: Flexible AOD used for strict shared-array transfers.
        catalog: Isolated two-trap cells distributed across the shared array.
        max_cz_gates: Optional layer limit; None uses the cell count.
        max_measurements: Readout layer limit, normally taken from Device.

    The caller supplies the reference through compile_physical.entry_state.
    Every lower call restores its entry layout; no mutable placement or
    measurement state is retained between calls or circuit instances.
    """

    aod_id: str
    catalog: GatePairCatalog
    max_cz_gates: int | None = None
    max_measurements: int | None = None

    @property
    def lookahead_enabled(self) -> bool:
        """Fixed-home layers do not request or consume speculative lookahead."""
        return False

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Select only operations admitted by the existing DAG controller."""
        return ReadyScheduler(
            self.catalog.capacity if self.max_cz_gates is None else self.max_cz_gates,
            max_measurements=self.max_measurements,
        ).select(dag, ready)

    def lower(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> AtomFragment:
        """Prepare all pairs, pulse once, and undo selected transport batches."""
        if not layer.operation_ids:
            return AtomFragment((), layout)
        name = dag.nodes[layer.operation_ids[0]].name
        require_capabilities(device, self.aod_id, readout=name == "measure")
        if name == "measure":
            return BatchReadout(self.aod_id).lower(dag, layer, layout, device)
        if name != "cz":
            return emit_layer(dag, layer, layout, device)
        plan = plan_layer(dag, layer, layout, device, self.catalog, self.aod_id)
        arrival = tuple(
            step
            for batch in plan.batches
            for step in emit_batch(batch, layout, device, self.aod_id)
        )
        pulse = WholeLayerEmitter(self.catalog.coverage)(
            dag, layer, plan.execution_layout, device
        )
        departure = tuple(
            step
            for batch in reversed(plan.batches)
            for step in emit_batch(batch, layout, device, self.aod_id, returning=True)
        )
        return AtomFragment(
            arrival + pulse.steps + departure, layout, layer.operation_ids
        )
