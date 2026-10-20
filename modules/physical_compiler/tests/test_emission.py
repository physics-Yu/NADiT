"""Direct Atom IR construction from selected native gate layers."""

import pytest

from nadit_physical_compiler import motion
from nadit_physical_compiler.atom_ir import CZPulse, Measure, Reset, Rotation
from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import (
    Device,
    Position,
    Region,
    Timing,
)
from nadit_physical_compiler.emission import emit_layer
from nadit_physical_compiler.physical_ir import (
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder


def _device() -> Device:
    return Device(
        "emit",
        (),
        (),
        (),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5, (("slm", "slm"),)),
        local=LocalControl("raman", 2, 3, ("slm", "aod")),
        readout=ReadoutCapability("camera", 10, latency_us=4),
    )


def _layout() -> AtomLayout:
    return AtomLayout(
        (
            AtomState(7, Position(2, 10), SLMHolder("g1")),
            AtomState(9, Position(0, 10), SLMHolder("g0")),
        ),
        qubit_to_atom=(7, 9),
    )


@pytest.mark.parametrize(("name", "axis"), [("rx", "X"), ("ry", "Y"), ("rz", "Z")])
def test_rotations_bind_atoms_angles_source_ids_and_parallel_timing(
    name: str, axis: str
) -> None:
    dag = PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, name, (0,), (), (0.5,), source_id=21),
            PhysicalOperation(1, name, (1,), (), (-0.25,), source_id=34),
        ),
        (),
    )
    layout = _layout()
    fragment = emit_layer(dag, GateLayer((0, 1)), layout, _device())
    actions = fragment.steps[0].actions

    assert len(actions) == 2
    assert all(isinstance(action, Rotation) for action in actions)
    assert isinstance(actions[0], Rotation)
    assert isinstance(actions[1], Rotation)
    assert actions[0].atoms == (7,)
    assert actions[1].atoms == (9,)
    assert (actions[0].angle, actions[1].angle) == (0.5, -0.25)
    assert actions[0].axis == actions[1].axis == axis
    assert actions[0].channel == "raman"
    assert actions[0].source_ids == (0,)
    assert actions[1].source_ids == (1,)
    assert fragment.steps[0].duration_us == 2
    assert fragment.completed_operation_ids == (0, 1)
    assert fragment.exit_layout is layout


def test_cz_emits_bounding_region_for_selected_pair() -> None:
    dag = PhysicalDAG(2, 0, (PhysicalOperation(0, "cz", (1, 0), (), ()),), ())
    fragment = emit_layer(dag, GateLayer((0,)), _layout(), _device())
    assert fragment.steps[0].actions == (
        CZPulse("rydberg", Region(0, 10, 2, 10), duration_us=0.5, source_ids=(0,)),
    )


def test_measurement_uses_stable_source_result_and_native_node_provenance() -> None:
    dag = PhysicalDAG(
        2,
        1,
        (PhysicalOperation(0, "measure", (1,), (0,), (), source_id=42),),
        (),
    )
    fragment = emit_layer(dag, GateLayer((0,)), _layout(), _device())
    assert fragment.steps[0].actions == (
        Measure(
            "camera",
            ((9, ResultRef(42)),),
            True,
            duration_us=10,
            source_ids=(0,),
        ),
    )
    assert fragment.completed_operation_ids == (0,)


def test_reset_emits_addressed_action() -> None:
    dag = PhysicalDAG(2, 0, (PhysicalOperation(0, "reset", (0,), (), ()),), ())
    fragment = emit_layer(dag, GateLayer((0,)), _layout(), _device())
    assert fragment.steps[0].actions == (
        Reset("raman", (7,), duration_us=3, source_ids=(0,)),
    )


def test_empty_layer_emits_no_steps() -> None:
    layout = _layout()
    fragment = emit_layer(PhysicalDAG(2, 0, (), ()), GateLayer(()), layout, _device())
    assert fragment.steps == ()
    assert fragment.completed_operation_ids == ()
    assert fragment.exit_layout is layout


def test_emission_does_not_recheck_selected_device_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_check(*args: object, **kwargs: object) -> None:
        raise AssertionError("Emission must not replan or validate movement.")

    monkeypatch.setattr(motion, "check_move", unexpected_check)
    dag = PhysicalDAG(2, 0, (PhysicalOperation(0, "reset", (0,), (), ()),), ())
    assert emit_layer(dag, GateLayer((0,)), _layout(), _device()).steps
