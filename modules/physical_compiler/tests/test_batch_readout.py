"""Batch readout preserves physical results, spacing, and return layouts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_strict_transport import _case

from nadit_physical_compiler import FakeBackend, loads_qasm
from nadit_physical_compiler.atom_ir import AtomProgram, Load, Measure, Move, Store
from nadit_physical_compiler.capabilities import LocalControl, ReadoutCapability
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import Device, Region, Zone, loads_device
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.physical_ir import PhysicalDAG, ResultRef
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.readout import BatchReadout
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.state import AtomLayout, validate_layout
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.transport import StrictTransport
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]


def setup_readout(
    sources: tuple[tuple[float, float], ...],
    *,
    capacity: int | None = None,
    stationary: tuple[tuple[float, float], ...] = (),
    height: float = 20,
    columns: int | None = None,
) -> tuple[Device, AtomLayout]:
    device, entry, _ = _case(sources, sources, stationary=stationary, columns=columns)
    return replace(
        device,
        zones=(Zone("measurement", Region(0, 40, 50, 40 + height), ("measure",)),),
        readout=ReadoutCapability("camera", 10, 500, 5, True, ("aod",), capacity),
    ), entry


def measure_dag(n: int) -> PhysicalDAG:
    return loads_qasm(
        'OPENQASM 3.0; include "stdgates.inc"; '
        f"qubit[{n}] q; bit[{n}] c; c = measure q;"
    )


@pytest.mark.parametrize(
    "capacity,expected", [(None, [4]), (2, [2, 2]), (1, [1, 1, 1, 1])]
)
def test_readout_capacity_and_exact_return(
    capacity: int | None, expected: list[int]
) -> None:
    device, entry = setup_readout(((0, 0), (5, 0), (10, 0), (15, 0)), capacity=capacity)
    dag = measure_dag(4)
    layer = GateLayer(tuple(node.id for node in dag.nodes))
    fragment = BatchReadout("a").lower(dag, layer, entry, device)
    measurements = [
        a for s in fragment.steps for a in s.actions if isinstance(a, Measure)
    ]
    assert [len(a.results) for a in measurements] == expected
    assert all(a.duration_us == 10 for a in measurements)
    assert {r for a in measurements for _, r in a.results} == {
        n.result for n in dag.nodes
    }
    current = entry
    for step in fragment.steps:
        action = step.actions[0]
        if isinstance(action, Load):
            checked = check_load(
                device, current, action.grid, {a: (r, c) for a, r, c in action.bindings}
            )
        elif isinstance(action, Move):
            checked = check_move(device, current, action.grid, dict(action.targets))
        elif isinstance(action, Store):
            checked = check_store(device, current, action.grid, dict(action.bindings))
        else:
            assert isinstance(action, Measure)
            continue
        assert checked.ok, checked
        current = apply_step(current, step, device)
        validate_layout(device, current)
    assert current == entry == fragment.exit_layout
    assert fragment.completed_operation_ids == layer.operation_ids


@pytest.mark.parametrize("height,columns", [(0, None), (5, None), (20, 1)])
def test_rows_and_columns_split_by_device_geometry(
    height: float, columns: int | None
) -> None:
    device, entry = setup_readout(
        ((0, 0), (5, 0), (0, 5), (5, 5)), height=height, columns=columns
    )
    dag = measure_dag(4)
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_measurements=None),
            PairPlacer("a"),
            StrictTransport("a"),
            readout=BatchReadout("a"),
        ),
        entry_state=entry,
        measurement_backend=FakeBackend(17),
    )
    report = validate_result(result, entry_state=entry, device=device, dag=dag)
    assert report["ok"], report
    assert replay(result.program, entry, device) == entry
    exposures = [
        a for s in result.program.steps for a in s.actions if isinstance(a, Measure)
    ]
    assert len(exposures) == (1 if height == 5 and columns is None else 2)


def test_source_ghost_never_picks_up_unmeasured_data_atom() -> None:
    device, entry = setup_readout(((0, 0), (5, 5)), stationary=((0, 5),))
    dag = measure_dag(2)
    fragment = BatchReadout("a").lower(dag, GateLayer((0, 1)), entry, device)
    loads = [a for s in fragment.steps for a in s.actions if isinstance(a, Load)]
    assert len(loads) == 2
    assert {atom for a in loads for atom, _, _ in a.bindings} == {0, 1}
    assert replay(AtomProgram(fragment.steps), entry, device) == entry


@pytest.mark.parametrize("bit", [0, 1])
def test_registry_feedback_and_result_latency(bit: int) -> None:
    base = load_job(MODULE / "examples/jobs/multipair.json")
    document = base.document
    assert base.device.readout is not None
    device = replace(base.device, readout=replace(base.device.readout, max_atoms=None))
    document["device"] = encode_device(device)
    document["case"]["overrides"] = {
        str(n.result.measurement_id): bit
        for n in base.dag.nodes
        if n.result is not None
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)
    result = compile_job(job)
    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"], report
    assert replay(result.program, job.entry_state, job.device) == result.exit_state
    records = {m.result: m for m in result.measurements}
    assert len(records) == len(document["case"]["overrides"])
    ops = {op.operation_id: op for op in result.operations}
    for node in job.dag.nodes:
        if node.condition is not None and node.condition.value == bit:
            assert (
                ops[node.id].start_us >= records[node.condition.result].available_at_us
            )


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_invalid_readout_capacity(value: object) -> None:
    raw = encode_device(load_job(MODULE / "examples/jobs/multipair.json").device)
    raw["readout"]["max_atoms"] = value
    with pytest.raises(ValueError, match="max_atoms"):
        loads_device(json.dumps(raw))


def test_rigid_batch_readout_is_unsupported() -> None:
    base = load_job(MODULE / "examples/jobs/multipair.json")
    raw = encode_device(base.device)
    raw["readout"]["max_atoms"] = None
    raw["aods"][0]["motion"] = "rigid"
    document = base.document
    document["device"] = raw
    with pytest.raises(UnsupportedJob, match="flexible"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_ready_ghost_closure_and_occupied_readout_corners() -> None:
    device, entry = setup_readout(
        ((0, 0), (5, 5), (0, 5)), stationary=((0, 40), (50, 40), (0, 60), (50, 60))
    )
    dag = measure_dag(3)
    fragment = BatchReadout("a").lower(dag, GateLayer((0, 1, 2)), entry, device)
    assert [
        len(a.results)
        for s in fragment.steps
        for a in s.actions
        if isinstance(a, Measure)
    ] == [3]
    assert replay(AtomProgram(fragment.steps), entry, device) == entry
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_measurements=None),
            PairPlacer("a"),
            StrictTransport("a"),
            readout=BatchReadout("a"),
        ),
        entry_state=entry,
    )
    assert validate_result(result, entry_state=entry, device=device, dag=dag)["ok"]


@pytest.mark.parametrize("bits", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_batched_results_sample_once_and_feedback_waits(bits: tuple[int, int]) -> None:
    device, entry = setup_readout(((0, 0), (5, 0), (10, 0)))
    device = replace(
        device,
        local=LocalControl("raman", 1, 5),
        zones=(
            *device.zones,
            Zone("storage", Region(-1, -1, 20, 20), ("rotate", "reset")),
        ),
    )
    dag = loads_qasm(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[3] q; bit[2] c; '
        "c[0] = measure q[0]; c[1] = measure q[1]; "
        "if (c[0] == true) { x q[2]; } if (c[1] == true) { z q[2]; }"
    )
    refs = [n.result for n in dag.nodes if n.result is not None]
    values = dict(zip(refs, bits, strict=True))
    calls: list[ResultRef] = []

    class Backend:
        def measure(self, result: ResultRef) -> int:
            calls.append(result)
            return values[result]

    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_measurements=None),
            PairPlacer("a"),
            StrictTransport("a"),
            readout=BatchReadout("a"),
        ),
        entry_state=entry,
        measurement_backend=Backend(),
    )
    assert calls == refs
    exposures = [
        (start, a)
        for start, s in result.program.timeline()
        for a in s.actions
        if isinstance(a, Measure)
    ]
    assert len(exposures) == 1 and len(exposures[0][1].results) == 2
    assert all(
        m.available_at_us == exposures[0][0] + 10 + 500 for m in result.measurements
    )
    checked = validate_result(result, entry_state=entry, device=device, dag=dag)
    assert checked["ok"], checked
    assert replay(result.program, entry, device) == entry
    assert device.readout is not None
    limited = replace(device, readout=replace(device.readout, max_atoms=1))
    rejected = validate_result(result, entry_state=entry, device=limited, dag=dag)
    assert not rejected["ok"]
    assert any(item["rule"] == "readout_capacity" for item in rejected["failures"])


def test_repeated_measurement_identities_and_zero_move() -> None:
    device, entry = setup_readout(((0, 40), (5, 40)))
    dag = loads_qasm(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[2] q; bit[3] c; '
        "c[0] = measure q[0]; c[1] = measure q[1]; barrier q; c[2] = measure q[0];"
    )
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_measurements=None),
            PairPlacer("a"),
            StrictTransport("a"),
            readout=BatchReadout("a"),
        ),
        entry_state=entry,
    )
    assert len({m.result for m in result.measurements}) == 3
    assert not any(isinstance(a, Move) for s in result.program.steps for a in s.actions)
    assert validate_result(result, entry_state=entry, device=device, dag=dag)["ok"]
    assert replay(result.program, entry, device) == entry
