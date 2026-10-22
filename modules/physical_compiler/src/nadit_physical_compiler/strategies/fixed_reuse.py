"""Opt-in QEC windows using fixed homes and the existing reuse/transport stack."""

from dataclasses import dataclass, replace

from ..contracts import AtomFragment, GateLayer
from ..device import Device
from ..physical_ir import PhysicalDAG
from ..placement.fixed_reuse import FixedReusePlacer
from ..placement.heuristic import HeuristicPlacer
from ..placement.multipair import MultiPairPlacer
from ..state import AtomLayout, AtomState, SLMHolder
from ..support import UnsupportedJob
from .search_smarter import SearchSmarterStrategy


@dataclass(frozen=True, slots=True)
class FixedReuseStrategy(SearchSmarterStrategy):
    """A fresh bound instance owns only immutable homes, never DAG/measurement state."""

    home_layout: AtomLayout | None = None

    def __post_init__(self) -> None:
        if self.mode != "qec" or self.reuse_analyzer is None:
            raise ValueError(
                "Cross-layer reuse requires QEC mode and a reuse analyzer."
            )
        if not isinstance(self.placer, (HeuristicPlacer, MultiPairPlacer)):
            raise ValueError("Cross-layer reuse requires a catalogue execution placer.")

    def bind_entry(self, entry: AtomLayout) -> "FixedReuseStrategy":
        """Bind fixed SLM homes once per compilation without mutating this strategy."""
        assert isinstance(self.placer, (HeuristicPlacer, MultiPairPlacer))
        if any(
            not isinstance(a.holder, SLMHolder)
            or self.placer.catalog.coverage.contains(a.position)
            for a in entry.atoms
        ):
            raise UnsupportedJob(
                [
                    {
                        "stage": "entry",
                        "kind": "cross_layer_reuse",
                        "reason": "requires SLM homes outside CZ pulse coverage",
                    }
                ]
            )
        return replace(self, home_layout=entry)

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
        """Retain across known CZ/rotation chains; close homes at readout/reset."""
        if self.home_layout is None:
            raise ValueError("Fixed reuse strategy must be bound by compile_physical.")
        assert isinstance(self.placer, (HeuristicPlacer, MultiPairPlacer))
        home = self.home_layout
        boundary = bool(layer.operation_ids) and dag.nodes[
            layer.operation_ids[0]
        ].name in ("measure", "reset")
        if boundary:
            flush = self.transporter.construct(layout, home, device)
            plain = SearchSmarterStrategy(
                self.scheduler,
                self.placer,
                self.transporter,
                self.emitter,
                None,
                self.readout,
                mode="qec",
            )
            operation = plain.lower(dag, layer, home, device)
            return replace(operation, steps=flush.steps + operation.steps)
        delegate = SearchSmarterStrategy(
            self.scheduler,
            FixedReusePlacer(self.placer, home),
            self.transporter,
            self.emitter,
            self.reuse_analyzer,
            self.readout,
        )
        return delegate.lower(
            dag,
            layer,
            layout,
            device,
            following=following,
            retained_atoms=retained_atoms,
        )
