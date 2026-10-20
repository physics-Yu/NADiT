"""Strategy hands a known next layer to placement without sampling."""

from nadit_physical_compiler import (
    AtomFragment,
    AtomLayout,
    AtomState,
    Device,
    GateLayer,
    PhysicalDAG,
    PhysicalOperation,
    PlacementPlan,
    Position,
    SLMHolder,
    Timing,
)
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy


def test_strategy_passes_the_known_layer_and_reuse_candidates_to_placement() -> None:
    dag = PhysicalDAG(
        2,
        0,
        tuple(PhysicalOperation(i, "cz", (0, 1), (), ()) for i in range(2)),
        ((0, 1),),
    )
    current, following = GateLayer((0,)), GateLayer((1,))
    entry = AtomLayout(
        (
            AtomState(0, Position(0, 0), SLMHolder("left")),
            AtomState(1, Position(2, 0), SLMHolder("right")),
        ),
        qubit_to_atom=(0, 1),
    )
    device = Device("handoff", (), (), (), Timing(1, 1, 1))
    received: list[tuple[GateLayer, GateLayer | None, tuple[int, ...]]] = []

    class Placer:
        def place(
            self,
            dag: PhysicalDAG,
            layer: GateLayer,
            layout: AtomLayout,
            device: Device,
            *,
            reuse_qubits: tuple[int, ...] = (),
            following: GateLayer | None = None,
            retained_atoms: tuple[AtomState, ...] = (),
        ) -> PlacementPlan:
            received.append((layer, following, reuse_qubits))
            return PlacementPlan(layout, layout)

    class Transport:
        def construct(
            self, layout: AtomLayout, target_layout: AtomLayout, device: Device
        ) -> AtomFragment:
            return AtomFragment((), target_layout)

    strategy = SearchSmarterStrategy(
        ReadyScheduler(),
        Placer(),
        Transport(),
        lambda dag, layer, layout, device: AtomFragment(
            (), layout, layer.operation_ids
        ),
        MatchingReuseAnalyzer(),
    )
    fragment = strategy.lower(dag, current, entry, device, following=following)
    assert received == [(current, following, (0, 1))]
    assert fragment.completed_operation_ids == (0,)
