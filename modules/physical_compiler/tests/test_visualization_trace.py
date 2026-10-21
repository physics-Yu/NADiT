"""Replay traces preserve compiler timing, ownership, and illumination."""

import json
from dataclasses import replace
from typing import Any

import pytest

from nadit_physical_compiler.atom_ir import (
    AtomProgram,
    AtomStep,
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
    Wait,
)
from nadit_physical_compiler.capabilities import CZCapability
from nadit_physical_compiler.compiler import CompiledOperation, MeasurementRecord
from nadit_physical_compiler.coverage import RegionSet
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
)
from nadit_physical_compiler.physical_ir import ResultRef
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
)
from nadit_physical_compiler.visualization import build_trace
from nadit_physical_compiler.visualization import trace as trace_module


@pytest.fixture
def device() -> Device:
    return Device(
        "trace device",
        (Zone("work", Region(-2, -2, 30, 30), ("cz", "measure", "rotate")),),
        (
            SLMSite("s0", Position(0, 0)),
            SLMSite("s1", Position(5, 0)),
            SLMSite("g0", Position(0, 20)),
            SLMSite("g1", Position(2, 20)),
        ),
        (
            AOD("a", Region(-2, -2, 30, 30), 1, 16, 16),
            AOD("b", Region(-2, -2, 30, 30), 1),
        ),
        Timing(2, 3, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )


def _entry() -> AtomLayout:
    return AtomLayout(
        (
            AtomState(42, Position(0, 0), SLMHolder("s0")),
            AtomState(19, Position(5, 0), SLMHolder("s1")),
        ),
        qubit_to_atom=(19, 42),
    )


def _grid(y: float, right: float) -> AODGrid:
    return AODGrid("a", (AODLine(8, y),), (AODLine(4, 0), AODLine(2, right)))


def _transport_program() -> AtomProgram:
    return AtomProgram(
        (
            AtomStep((Load(_grid(0, 5), ((42, 8, 4), (19, 8, 2)), duration_us=2),)),
            AtomStep(
                (
                    Move(
                        _grid(20, 2),
                        ((42, Position(0, 20)), (19, Position(2, 20))),
                        duration_us=20,
                        source_ids=(4,),
                    ),
                )
            ),
            AtomStep((Store(AODGrid("a"), ((42, "g0"), (19, "g1")), duration_us=3),)),
        ),
        global_phase=0.2,
    )


def _record_layout(atoms: dict[int, Any], grids: dict[str, Any]) -> AtomLayout:
    restored = []
    for atom in atoms.values():
        holder = atom["holder"]
        restored.append(
            AtomState(
                atom["id"],
                Position(atom["x"], atom["y"]),
                SLMHolder(holder["site_id"])
                if holder["kind"] == "slm"
                else AODHolder(holder["aod_id"], holder["row"], holder["column"]),
            )
        )
    return AtomLayout(
        tuple(restored),
        tuple(
            AODGrid(
                grid["aod_id"],
                tuple(AODLine(**line) for line in grid["rows"]),
                tuple(AODLine(**line) for line in grid["columns"]),
            )
            for grid in grids.values()
        ),
        (19, 42),
    )


def test_sparse_patches_reconstruct_every_replay_boundary(device: Device) -> None:
    program = _transport_program()
    trace = build_trace(program, _entry(), device, title="Transport")
    assert trace["schema_version"] == 1
    assert trace["atom_ir"] == program.to_text()
    assert trace["program_duration_us"] == trace["duration_us"] == 25
    assert [step["start_us"] for step in trace["steps"]] == [0, 2, 22]
    atoms = {atom["id"]: atom for atom in trace["initial"]["atoms"]}
    grids = {grid["aod_id"]: grid for grid in trace["initial"]["grids"]}
    layout = _entry()
    for emitted, step in zip(trace["steps"], program.steps, strict=True):
        for action in emitted["actions"]:
            for atom in action["patch"]["atoms"]:
                atoms[atom["id"]] = atom
            for grid in action["patch"]["grids"]:
                if grid["rows"] or grid["columns"]:
                    grids[grid["aod_id"]] = grid
                else:
                    grids.pop(grid["aod_id"], None)
        layout = apply_step(layout, step, device)
        assert _record_layout(atoms, grids) == layout
    move = trace["steps"][1]["actions"][0]
    assert move["source_ids"] == [4]
    assert move["index"] == 1
    assert move["text"].startswith("MOVE(")
    assert move["patch"]["grids"][0]["rows"] == [{"id": 8, "coordinate": 20}]
    assert [line["id"] for line in move["patch"]["grids"][0]["columns"]] == [4, 2]
    assert trace["steps"][2]["actions"][0]["patch"]["grids"] == [
        {"aod_id": "a", "rows": [], "columns": []}
    ]
    assert trace["issues"] == []
    assert json.loads(json.dumps(trace)) == trace


def test_parallel_actions_use_common_entry_and_keep_individual_durations(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[AtomStep] = []

    def tracked(layout: AtomLayout, step: AtomStep, device: Device) -> AtomLayout:
        calls.append(step)
        return apply_step(layout, step, device)

    monkeypatch.setattr(trace_module, "apply_step", tracked)
    a = AODGrid("a", (AODLine(8, 0),), (AODLine(4, 0),))
    b = AODGrid("b", (AODLine(3, 0),), (AODLine(9, 5),))
    actions = (
        Load(a, ((42, 8, 4),), duration_us=2),
        Load(b, ((19, 3, 9),), duration_us=7),
    )
    forward = build_trace(
        AtomProgram((AtomStep(actions),)), _entry(), device, title="A"
    )
    backward = build_trace(
        AtomProgram((AtomStep(tuple(reversed(actions))),)), _entry(), device, title="B"
    )
    assert len(calls) == 2
    assert forward["steps"][0]["duration_us"] == 7
    emitted = forward["steps"][0]["actions"]
    assert [action["duration_us"] for action in emitted] == [2, 7]
    for first, second in zip(
        emitted, reversed(backward["steps"][0]["actions"]), strict=True
    ):
        assert first["patch"] == second["patch"]
        assert len(first["patch"]["atoms"]) == len(first["patch"]["grids"]) == 1


def test_cz_union_addressing_radius_and_holder_pairs(device: Device) -> None:
    layout = AtomLayout(
        (
            AtomState(10, Position(0, 0), SLMHolder("s0")),
            AtomState(11, Position(2, 0), AODHolder("a", 0, 0)),
            AtomState(12, Position(3, 0), SLMHolder("s1")),
            AtomState(13, Position(10, 0), SLMHolder("g0")),
            AtomState(14, Position(12, 0), AODHolder("b", 0, 0)),
            AtomState(15, Position(6, 0), SLMHolder("g1")),
        )
    )
    device = replace(
        device, cz=CZCapability("rydberg", 4.6, 0.5, holder_pairs=(("aod", "slm"),))
    )
    coverage = RegionSet((Region(0, -1, 3, 1), Region(10, -1, 12, 1)))
    program = AtomProgram(
        (
            AtomStep((CZPulse("rydberg", coverage, duration_us=0.5),)),
            AtomStep((CZPulse("rydberg", (10, 11, 13), duration_us=0.5),)),
        )
    )
    trace = build_trace(program, layout, device, title="CZ")
    regional, addressed = [step["actions"][0] for step in trace["steps"]]
    assert regional["atoms"] == [10, 11, 12, 13, 14]
    assert regional["coverage"] == {
        "regions": [[0, -1, 3, 1], [10, -1, 12, 1]],
        "atoms": [10, 11, 12, 13, 14],
    }
    assert regional["pairs"] == [[10, 11], [11, 12], [13, 14]]
    assert addressed["coverage"] == {"regions": [], "atoms": [10, 11, 13]}
    assert addressed["pairs"] == [[10, 11]]
    assert regional["patch"] == addressed["patch"] == {"atoms": [], "grids": []}


def test_controls_measurement_latency_and_zero_duration_steps(device: Device) -> None:
    result = ResultRef(7)
    actions = (
        Rotation("local", (42,), "Y", 0.25, duration_us=1, source_ids=(3,)),
        LocalU("local", (19,), 0.1, 0.2, 0.3, duration_us=1),
        GlobalRY("global", Region(-1, -1, 1, 1), 0.75, duration_us=1),
        Reset("local", (19,), duration_us=1),
        Measure("camera", ((42, result),), True, duration_us=5),
        Wait(duration_us=0),
    )
    program = AtomProgram(
        tuple(AtomStep((action,)) for action in actions) + (AtomStep(),)
    )
    trace = build_trace(
        program,
        _entry(),
        device,
        title="Feedback",
        description="A fixed recorded outcome",
        measurements=(MeasurementRecord(result, 1, 16),),
        operations=(
            CompiledOperation(3, "emitted", 0, 1),
            CompiledOperation(8, "skipped", 16, 16),
        ),
    )
    assert trace["description"] == "A fixed recorded outcome"
    assert trace["duration_us"] == 16
    assert trace["program_duration_us"] == 9
    assert len(trace["steps"]) == 7
    assert trace["steps"][-1]["start_us"] == 9
    assert trace["steps"][-1]["actions"] == []
    exported = [step["actions"][0] for step in trace["steps"][:-1]]
    assert [action["atoms"] for action in exported] == [
        [42],
        [19],
        [42],
        [19],
        [42],
        [],
    ]
    assert exported[0]["parameters"] == {"channel": "local", "axis": "Y", "angle": 0.25}
    assert exported[4]["parameters"] == {
        "channel": "camera",
        "results": [{"atom_id": 42, "measurement_id": 7}],
        "retain_atoms": True,
    }
    assert trace["measurements"] == [
        {"measurement_id": 7, "value": 1, "available_at_us": 16}
    ]
    assert trace["operations"][1] == {
        "operation_id": 8,
        "status": "skipped",
        "start_us": 16,
        "end_us": 16,
    }


def test_endpoint_observations_include_vacant_cross_intersections(
    device: Device,
) -> None:
    layout = AtomLayout(
        (
            AtomState(42, Position(0, 0), SLMHolder("s0")),
            AtomState(19, Position(5, 0), AODHolder("a", 8, 2)),
        ),
        (_grid(0, 5),),
        (19, 42),
    )
    trace = build_trace(
        AtomProgram((AtomStep((Wait(duration_us=2),)),)),
        layout,
        device,
        title="Observed",
    )
    assert [
        (issue["step_index"], issue["time_us"], issue["atom_id"], issue["aod_id"])
        for issue in trace["issues"]
    ] == [(-1, 0, 42, "a"), (0, 2, 42, "a")]
    assert all(issue["kind"] == "occupied_intersection" for issue in trace["issues"])
    assert trace["initial"]["atoms"][0]["qubit"] == 1


def test_empty_program_exports_device_and_unbound_atoms(device: Device) -> None:
    trace = build_trace(
        AtomProgram(),
        replace(_entry(), qubit_to_atom=()),
        replace(device, cz=None),
        title="Empty",
    )
    assert trace["steps"] == []
    assert trace["duration_us"] == 0
    assert trace["device"]["cz_radius_um"] is None
    assert trace["device"]["zones"][0]["bounds"] == [-2, -2, 30, 30]
    assert trace["device"]["aods"][0]["max_columns"] == 16
    assert trace["device"]["aods"][1]["max_rows"] is None
    assert trace["initial"]["atoms"][0]["qubit"] is None


def test_destructive_measurement_preserves_replay_boundary(device: Device) -> None:
    program = AtomProgram(
        (AtomStep((Measure("camera", ((42, ResultRef(7)),), False, duration_us=2),)),)
    )
    with pytest.raises(NotImplementedError, match="retained"):
        build_trace(program, _entry(), device, title="Unsupported lifecycle")
