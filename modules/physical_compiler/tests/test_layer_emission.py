"""One pulse serves a placed CZ layer while other actions use existing emission."""

from dataclasses import replace
from itertools import combinations
from math import hypot

import pytest

from nadit_physical_compiler.atom_ir import AtomProgram, CZPulse, Measure
from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import Device, Position, Region, Timing
from nadit_physical_compiler.emission import WholeLayerEmitter, emit_layer
from nadit_physical_compiler.physical_ir import (
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder


def _device() -> Device:
    return Device(
        "whole-layer",
        (),
        (),
        (),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
        readout=ReadoutCapability("camera", 10),
        local=LocalControl("raman", 1, 2),
    )


def _layout() -> AtomLayout:
    return AtomLayout(
        tuple(
            AtomState(atom_id, Position(x, 20), SLMHolder(f"g{atom_id}"))
            for atom_id, x in ((10, 0), (20, 2), (30, 8), (40, 10))
        ),
        qubit_to_atom=(20, 10, 40, 30),
    )


def test_multi_cz_layer_emits_exactly_one_pulse_with_complete_provenance() -> None:
    dag = PhysicalDAG(
        4,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), (), source_id=100),
            PhysicalOperation(1, "cz", (2, 3), (), (), source_id=200),
        ),
        (),
    )
    layer = GateLayer((0, 1))
    layout = _layout()
    coverage = Region(-1, 19, 11, 21)
    emitter = WholeLayerEmitter(coverage)

    fragment = emitter(dag, layer, layout, _device())

    assert len(fragment.steps) == 1
    assert fragment.steps[0].actions == (
        CZPulse("rydberg", coverage, duration_us=0.5, source_ids=(0, 1)),
    )
    assert AtomProgram(fragment.steps).duration_us == 0.5
    assert fragment.completed_operation_ids == (0, 1)
    assert fragment.exit_layout is layout
    assert layout == _layout()
    assert layer.operation_ids == (0, 1)
    assert tuple(node.source_id for node in dag.nodes) == (100, 200)
    illuminated = [atom for atom in layout.atoms if coverage.contains(atom.position)]
    actual_pairs = {
        frozenset((first.id, second.id))
        for first, second in combinations(illuminated, 2)
        if hypot(
            first.position.x - second.position.x,
            first.position.y - second.position.y,
        )
        <= 4.6
    }
    expected_pairs = {
        frozenset(layout.qubit_to_atom[q] for q in dag.nodes[index].qubits)
        for index in layer.operation_ids
    }
    assert actual_pairs == expected_pairs


def test_single_cz_keeps_configured_catalog_coverage() -> None:
    coverage = Region(-5, 19, 15, 21)
    dag = PhysicalDAG(2, 0, (PhysicalOperation(0, "cz", (0, 1), (), ()),), ())
    layout = AtomLayout(_layout().atoms[:2], qubit_to_atom=(20, 10))

    fragment = WholeLayerEmitter(coverage)(dag, GateLayer((0,)), layout, _device())

    action = fragment.steps[0].actions[0]
    assert isinstance(action, CZPulse)
    assert action.coverage == coverage


def test_readout_delegation_keeps_stable_result_and_native_provenance() -> None:
    dag = PhysicalDAG(
        4,
        1,
        (PhysicalOperation(0, "measure", (2,), (0,), (), source_id=42),),
        (),
    )
    layer = GateLayer((0,))
    layout = _layout()
    device = _device()

    fragment = WholeLayerEmitter(Region(-1, 19, 11, 21))(dag, layer, layout, device)

    assert fragment == emit_layer(dag, layer, layout, device)
    assert fragment.steps[0].actions == (
        Measure(
            "camera", ((40, ResultRef(42)),), True, duration_us=10, source_ids=(0,)
        ),
    )


def test_local_layer_delegates_existing_parallel_action_semantics() -> None:
    dag = PhysicalDAG(
        4,
        0,
        (
            PhysicalOperation(0, "rx", (0,), (), (0.5,)),
            PhysicalOperation(1, "rx", (2,), (), (-0.25,)),
        ),
        (),
    )
    layer = GateLayer((0, 1))
    layout = _layout()
    device = _device()

    assert WholeLayerEmitter(Region(-1, 19, 11, 21))(
        dag, layer, layout, device
    ) == emit_layer(dag, layer, layout, device)


def test_empty_layer_has_no_pulse_or_completed_nodes() -> None:
    layout = _layout()

    fragment = WholeLayerEmitter(Region(0, 0, 0, 0))(
        PhysicalDAG(4, 0, (), ()), GateLayer(()), layout, _device()
    )

    assert fragment.steps == ()
    assert fragment.completed_operation_ids == ()
    assert fragment.exit_layout is layout


def test_cz_layer_requires_device_pulse_capability() -> None:
    dag = PhysicalDAG(4, 0, (PhysicalOperation(0, "cz", (0, 1), (), ()),), ())

    with pytest.raises(ValueError, match="CZ capability"):
        WholeLayerEmitter(Region(-1, 19, 11, 21))(
            dag, GateLayer((0,)), _layout(), replace(_device(), cz=None)
        )
