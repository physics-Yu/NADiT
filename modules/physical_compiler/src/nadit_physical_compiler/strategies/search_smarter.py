"""Compose execution placement, transport, pulses, and exit placement."""

from collections.abc import Callable
from dataclasses import dataclass

from ..contracts import (
    AtomFragment,
    GateLayer,
    Placer,
    ReuseAnalyzer,
    Scheduler,
    Transporter,
)
from ..device import Device
from ..emission import emit_layer
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout

type Emitter = Callable[[PhysicalDAG, GateLayer, AtomLayout, Device], AtomFragment]


@dataclass(frozen=True, slots=True)
class SearchSmarterStrategy:
    """Compose the Search Smarter stage sequence with replaceable helpers.

    Args:
        scheduler: Select resolved native operations.
        placer: Choose execution and exit layouts.
        transporter: Construct legal transfers between layouts.
        emitter: Bind native operations to Atom IR actions and durations.
        reuse_analyzer: Optional matching policy for the next known CZ layer.

    The compiler supplies lookahead without committing future operations.
    Placement decides which gate sites and holders to retain.
    """

    scheduler: Scheduler
    placer: Placer
    transporter: Transporter
    emitter: Emitter = emit_layer
    reuse_analyzer: ReuseAnalyzer | None = None

    @property
    def lookahead_enabled(self) -> bool:
        """Request a following CZ layer only when a reuse analyzer is configured."""
        return self.reuse_analyzer is not None

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Select a layer from native operations ready at the planned time."""
        return self.scheduler.select(dag, ready)

    def lower(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        following: GateLayer | None = None,
    ) -> AtomFragment:
        """Construct a fragment, retaining selected atoms for a known CZ layer."""
        reuse_qubits = (
            self.reuse_analyzer.analyze(dag, layer, following)
            if self.reuse_analyzer is not None and following is not None
            else ()
        )
        plan = self.placer.place(
            dag, layer, layout, device, reuse_qubits=reuse_qubits, following=following
        )
        arrival = self.transporter.construct(layout, plan.execution_layout, device)
        operation = self.emitter(dag, layer, arrival.exit_layout, device)
        departure = self.transporter.construct(
            operation.exit_layout, plan.exit_layout, device
        )
        return AtomFragment(
            arrival.steps + operation.steps + departure.steps,
            departure.exit_layout,
            operation.completed_operation_ids,
        )
