"""Typed module handoffs using existing physical and atom representations."""

from dataclasses import FrozenInstanceError

import pytest

from nadit_physical_compiler import motion, state
from nadit_physical_compiler.atom_ir import AtomProgram, AtomStep, Measure, Move
from nadit_physical_compiler.contracts import (
    AtomFragment,
    GateLayer,
    PlacementPlan,
    Placer,
    ReuseAnalyzer,
    Scheduler,
    Transporter,
)
from nadit_physical_compiler.device import Device, Position, Timing
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
)


def _layout(x: float) -> AtomLayout:
    return AtomLayout(
        (AtomState(7, Position(x, 0), AODHolder("aod", 0, 0)),),
        (AODGrid("aod", (AODLine(0, 0),), (AODLine(0, x),)),),
        (7,),
    )


def test_modules_compose_through_structural_protocols() -> None:
    dag = PhysicalDAG(
        1,
        0,
        (
            PhysicalOperation(0, "rx", (0,), (), (0.5,)),
            PhysicalOperation(1, "rz", (0,), (), (0.25,)),
        ),
        ((0, 1),),
    )
    device = Device("test", (), (), (), Timing(1, 1, 1))
    entry, target = _layout(0), _layout(5)
    move = Move(
        target.aod_grids[0],
        ((7, Position(5, 0)),),
        duration_us=5,
        source_ids=(0,),
    )
    steps = (AtomStep((move,)),)

    class SelectReady:
        def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
            assert dag.nodes[ready[0]].name == "rx"
            return GateLayer(ready)

    class ReuseSharedQubits:
        def analyze(
            self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
        ) -> tuple[int, ...]:
            current_qubits = {
                q for op in current.operation_ids for q in dag.nodes[op].qubits
            }
            following_qubits = {
                q for op in following.operation_ids for q in dag.nodes[op].qubits
            }
            return tuple(sorted(current_qubits & following_qubits))

    class SelectedPlacement:
        def place(
            self,
            dag: PhysicalDAG,
            layer: GateLayer,
            layout: AtomLayout,
            device: Device,
            *,
            reuse_qubits: tuple[int, ...] = (),
            following: GateLayer | None = None,
        ) -> PlacementPlan:
            assert dag.nodes[layer.operation_ids[0]].qubits == reuse_qubits
            assert layout is entry
            assert device.name == "test"
            return PlacementPlan(target, target)

    class SelectedTransport:
        def construct(
            self, layout: AtomLayout, target_layout: AtomLayout, device: Device
        ) -> AtomFragment:
            assert layout is entry
            assert target_layout is target
            assert device.timing.move_duration(5) == move.duration_us
            return AtomFragment(steps, target_layout)

    scheduler: Scheduler = SelectReady()
    reuse: ReuseAnalyzer = ReuseSharedQubits()
    placer: Placer = SelectedPlacement()
    transporter: Transporter = SelectedTransport()

    layer = scheduler.select(dag, (0,))
    reuse_qubits = reuse.analyze(dag, layer, GateLayer((1,)))
    plan = placer.place(dag, layer, entry, device, reuse_qubits=reuse_qubits)
    fragment = transporter.construct(entry, plan.execution_layout, device)

    assert layer.operation_ids == (0,)
    assert reuse_qubits == (0,)
    assert plan.exit_layout is target
    assert fragment.steps is steps
    assert fragment.exit_layout is target
    assert fragment.completed_operation_ids == ()


def test_fragment_preserves_node_ids_and_stable_measurement_identity() -> None:
    measurement = PhysicalOperation(0, "measure", (0,), (0,), (), source_id=42)
    condition = Condition(ResultRef(42))
    dag = PhysicalDAG(
        1,
        1,
        (
            measurement,
            PhysicalOperation(
                1, "rx", (0,), (), (0.5,), condition=condition, source_id=77
            ),
        ),
        ((0, 1),),
    )
    result = measurement.result
    assert result is not None
    action = Measure("readout", ((7, result),), True, duration_us=10, source_ids=(0,))
    fragment = AtomFragment((AtomStep((action,)),), _layout(0), (0,))

    assert fragment.completed_operation_ids == (dag.nodes[0].id,)
    assert action.source_ids == (dag.nodes[0].id,)
    assert dag.nodes[1].condition is not None
    assert action.results[0][1] == dag.nodes[1].condition.result
    assert "rec[42]" in AtomProgram(fragment.steps).to_text()


def test_handoffs_retain_references_without_device_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(0)
    ids = (3, 5)
    steps = (AtomStep(),)

    def unexpected_check(*args: object, **kwargs: object) -> None:
        raise AssertionError("Module handoffs must not validate device state.")

    monkeypatch.setattr(state, "validate_layout", unexpected_check)
    monkeypatch.setattr(motion, "check_load", unexpected_check)
    monkeypatch.setattr(motion, "check_move", unexpected_check)
    monkeypatch.setattr(motion, "check_store", unexpected_check)
    layer = GateLayer(ids)
    plan = PlacementPlan(layout, layout)
    fragment = AtomFragment(steps, layout, ids)

    assert layer.operation_ids is ids
    assert plan.execution_layout is layout
    assert plan.exit_layout is layout
    assert fragment.steps is steps
    assert fragment.exit_layout is layout
    assert fragment.completed_operation_ids is ids


def test_empty_handoffs_represent_no_selected_work() -> None:
    layout = _layout(0)
    assert GateLayer(()).operation_ids == ()
    fragment = AtomFragment((), layout)
    assert AtomProgram(fragment.steps).duration_us == 0
    assert fragment.exit_layout is layout
    assert fragment.completed_operation_ids == ()


def test_gate_layer_is_immutable() -> None:
    layer = GateLayer((0,))
    with pytest.raises(FrozenInstanceError):
        layer.operation_ids = (1,)  # type: ignore[misc]


def test_placement_plan_is_immutable() -> None:
    plan = PlacementPlan(_layout(0), _layout(5))
    with pytest.raises(FrozenInstanceError):
        plan.exit_layout = plan.execution_layout  # type: ignore[misc]


def test_atom_fragment_is_immutable() -> None:
    fragment = AtomFragment((), _layout(0))
    with pytest.raises(FrozenInstanceError):
        fragment.completed_operation_ids = (0,)  # type: ignore[misc]
