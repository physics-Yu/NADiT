"""ASAP local layers expose disjoint CZ gates without sampling future feedback."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_ids_compiler import _strategy
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler.atom_ir import CZPulse
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.contracts import AtomFragment, GateLayer
from nadit_physical_compiler.device import Device, load_device
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.state import AtomLayout

MODULE = Path(__file__).parents[1]


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


@pytest.mark.parametrize("name", ["rx", "ry", "rz"])
def test_ready_local_rotation_precedes_an_earlier_independent_cz(name: str) -> None:
    dag = PhysicalDAG(
        4,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, name, (2,), (), (0.5,)),
            PhysicalOperation(2, "cz", (2, 3), (), ()),
        ),
        ((1, 2),),
    )
    scheduler = ReadyScheduler(max_cz_gates=2)

    assert scheduler.select(dag, (0, 1)).operation_ids == (1,)
    assert scheduler.select(dag, (0, 2)).operation_ids == (0, 2)


@pytest.mark.parametrize("name", ["measure", "reset", "barrier"])
def test_earliest_measurement_reset_or_barrier_keeps_its_position(name: str) -> None:
    dag = PhysicalDAG(
        4,
        1,
        (
            PhysicalOperation(0, name, (0,), (0,) if name == "measure" else (), ()),
            PhysicalOperation(1, "cz", (1, 2), (), ()),
            PhysicalOperation(2, "rx", (3,), (), (0.5,)),
        ),
        (),
    )

    assert ReadyScheduler(max_cz_gates=2).select(dag, (2, 1, 0)).operation_ids == (0,)


@pytest.mark.parametrize("local_names", [("rx",), ("rx", "ry", "rz")])
def test_local_chain_exposes_a_second_cz_for_one_pulse(
    device: Device, local_names: tuple[str, ...]
) -> None:
    last = len(local_names) + 1
    dag = PhysicalDAG(
        4,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            *(
                PhysicalOperation(index, name, (2,), (), (0.5,))
                for index, name in enumerate(local_names, start=1)
            ),
            PhysicalOperation(last, "cz", (2, 3), (), ()),
        ),
        tuple((index, index + 1) for index in range(1, last)),
    )
    entry = _entry(device)
    backend = RecordingBackend(1)

    result = compile_physical(
        dag,
        device,
        strategy=_strategy(device, "fixed"),
        entry_state=entry,
        measurement_backend=backend,
    )

    _audit(dag, result, device, entry)
    pulses = [
        action
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    ]
    assert len(pulses) == 1
    assert pulses[0].source_ids == (0, last)
    assert [layer.operation_ids for layer in result.layers] == [
        *((index,) for index in range(1, last)),
        (0, last),
    ]
    assert backend.calls == []


@pytest.mark.parametrize("bit", [0, 1])
def test_unknown_conditional_rotation_does_not_trigger_future_sampling(
    device: Device, bit: int
) -> None:
    reference = ResultRef(91)
    dag = PhysicalDAG(
        4,
        1,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "measure", (2,), (0,), (), source_id=91),
            PhysicalOperation(2, "rx", (3,), (), (0.5,), Condition(reference)),
            PhysicalOperation(3, "cz", (2, 3), (), ()),
            PhysicalOperation(4, "cz", (0, 1), (), ()),
        ),
        ((0, 4), (1, 2), (1, 3), (2, 3)),
    )
    entry = _entry(device)
    backend = RecordingBackend(bit)
    strategy = _strategy(device, "fixed")
    observations: list[tuple[tuple[int, ...], tuple[ResultRef, ...]]] = []

    def observed_emitter(
        dag: PhysicalDAG, layer: GateLayer, layout: AtomLayout, device: Device
    ) -> AtomFragment:
        observations.append((layer.operation_ids, tuple(backend.calls)))
        return strategy.emitter(dag, layer, layout, device)

    result = compile_physical(
        dag,
        device,
        strategy=replace(strategy, emitter=observed_emitter),
        entry_state=entry,
        measurement_backend=backend,
    )

    _audit(dag, result, device, entry)
    assert observations[0] == ((0,), ())
    assert backend.calls == [reference]
    assert result.operations[2].status == ("emitted" if bit else "skipped")
    assert result.operations[2].start_us >= result.measurements[0].available_at_us
    assert all(calls == (reference,) for layer, calls in observations if 2 in layer)
