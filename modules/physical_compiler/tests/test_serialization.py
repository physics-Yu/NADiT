"""Versioned JSON preserves executable Atom IR and compilation records."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from examples.atom_ir_example import program as example_program
from examples.viewer_cases import compile_feedback
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
from nadit_physical_compiler.capabilities import GlobalControl
from nadit_physical_compiler.compiler import (
    CompilationResult,
    CompiledOperation,
    MeasurementRecord,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.coverage import RegionSet
from nadit_physical_compiler.device import Position, Region, load_device, loads_device
from nadit_physical_compiler.physical_ir import ResultRef
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.serialization import (
    collect_provenance,
    decode_layout,
    decode_program,
    decode_result,
    encode_device,
    encode_layout,
    encode_program,
    encode_result,
)
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
)


def _wire(value: dict[str, Any]) -> dict[str, Any]:
    """Exercise actual JSON storage, including tuple-to-array conversion."""
    result: dict[str, Any] = json.loads(json.dumps(value, allow_nan=False))
    return result


@pytest.fixture
def layout() -> AtomLayout:
    return AtomLayout(
        (
            AtomState(17, Position(1.5, 8), AODHolder("transport", 3, 9)),
            AtomState(22, Position(20, 30), SLMHolder("storage_r0_c0")),
        ),
        (
            AODGrid("transport", (AODLine(3, 8),), (AODLine(9, 1.5),)),
            AODGrid("vacant"),
        ),
        (22, 17),
    )


@pytest.fixture
def all_actions() -> AtomProgram:
    grid = AODGrid("transport", (AODLine(3, 8),), (AODLine(9, 1.5),))
    region = Region(-1, 2, 10, 12)
    regions = RegionSet((region, Region(20, 20, 30, 30)))
    actions = (
        Load(grid, ((17, 3, 9),), duration_us=2, source_ids=(11, 13)),
        Move(grid, ((17, Position(1.5, 8)),), duration_us=3, source_ids=(11,)),
        Store(AODGrid("transport"), ((17, "storage_r0_c0"),), duration_us=2),
        CZPulse("rydberg", region, duration_us=0.5, source_ids=(4,)),
        CZPulse("rydberg", regions, duration_us=0.5, source_ids=(5, 6)),
        CZPulse("addressed", (17, 22), duration_us=0.5, source_ids=(7,)),
        Rotation("raman", (22, 17), "X", 1.25, duration_us=1, source_ids=(8,)),
        LocalU("raman", (17,), 1, 2, 3, duration_us=1, source_ids=(9,)),
        GlobalRY("global", region, 1.5, duration_us=1, source_ids=(10,)),
        GlobalRY("global", regions, 2.5, duration_us=1, source_ids=(11,)),
        Reset("reset", (22,), duration_us=3, source_ids=(12,)),
        Measure("camera", ((22, ResultRef(31)),), False, duration_us=5),
        Wait(duration_us=9, source_ids=(14,)),
    )
    return AtomProgram((AtomStep(actions), AtomStep()), 0.75)


def test_every_action_and_coverage_round_trip(all_actions: AtomProgram) -> None:
    assert decode_program(_wire(encode_program(all_actions))) == all_actions


def test_existing_example_program_round_trip() -> None:
    assert decode_program(_wire(encode_program(example_program))) == example_program


def test_empty_program_round_trip() -> None:
    assert decode_program(_wire(encode_program(AtomProgram()))) == AtomProgram()


def test_layout_round_trip(layout: AtomLayout) -> None:
    assert decode_layout(_wire(encode_layout(layout))) == layout


def test_empty_layout_round_trip() -> None:
    assert decode_layout(_wire(encode_layout(AtomLayout(())))) == AtomLayout(())


def test_result_records_round_trip(
    all_actions: AtomProgram, layout: AtomLayout
) -> None:
    result = CompilationResult(
        all_actions,
        replace(layout, qubit_to_atom=(17, 22)),
        (
            CompiledOperation(0, "emitted", 2, 8),
            CompiledOperation(1, "skipped", 9, 9),
            CompiledOperation(2, "metadata", 10, 10),
        ),
        (MeasurementRecord(ResultRef(31), 1, 500),),
        (GateLayer((0, 2)), GateLayer((1,))),
    )
    job = {"schema": "nadit.job/1", "case": {"seed": 17}}
    encoded = encode_result(result, entry_state=layout, job=job, provenance={})
    document = _wire(encoded)
    assert document["schema"] == "nadit.result/1"
    assert document["job"] == job
    assert "validation" not in document
    assert decode_result(document) == result
    assert decode_layout(document["entry_state"]) == layout


def test_result_accepts_metrics_and_validation(layout: AtomLayout) -> None:
    result = CompilationResult(AtomProgram(), layout, (), (), ())
    document = encode_result(
        result,
        entry_state=layout,
        job={},
        provenance={"git_commit": "fixture"},
        metrics={"atom_steps": 0},
        validation={"ok": True},
    )
    assert document["metrics"] == {"atom_steps": 0}
    assert document["validation"] == {"ok": True}
    assert document["provenance"] == {"git_commit": "fixture"}


@pytest.mark.parametrize("schema", ["nadit.result/2", "nadit.job/1", None])
def test_result_rejects_unknown_schema(schema: str | None) -> None:
    with pytest.raises(ValueError, match="schema"):
        decode_result({"schema": schema})


def test_program_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="action kind.*Teleport"):
        decode_program(
            {"global_phase": 0, "steps": [{"actions": [{"kind": "Teleport"}]}]}
        )


def test_program_rejects_unknown_coverage(all_actions: AtomProgram) -> None:
    encoded = encode_program(all_actions)
    encoded["steps"][0]["actions"][3]["coverage"]["kind"] = "beam"
    with pytest.raises(ValueError, match="coverage kind.*beam"):
        decode_program(encoded)


def test_layout_rejects_unknown_holder(layout: AtomLayout) -> None:
    encoded = encode_layout(layout)
    encoded["atoms"][0]["holder"]["kind"] = "trap"
    with pytest.raises(ValueError, match="holder kind.*trap"):
        decode_layout(encoded)


@pytest.mark.parametrize("name", ["one_pair_flexible", "multipair_flexible"])
def test_expanded_device_round_trip(name: str) -> None:
    device = load_device(Path(__file__).parents[1] / "configs" / f"{name}.json")
    device = replace(
        device,
        global_control=GlobalControl("global", 1, ("slm",), (0, 0, 50, 60)),
    )
    encoded = encode_device(device)
    assert "_sites_by_id" not in encoded
    assert "slm_grids" not in encoded
    assert loads_device(json.dumps(encoded)) == device


@pytest.mark.parametrize("bit", [0, 1])
def test_compiled_feedback_round_trip_and_replay(bit: int) -> None:
    example = compile_feedback(bit)
    document = _wire(
        encode_result(example.result, entry_state=example.entry, job={}, provenance={})
    )
    restored = decode_result(document)
    entry = decode_layout(document["entry_state"])
    assert restored == example.result
    assert replay(restored.program, entry, example.device) == restored.exit_state


def test_provenance_has_stable_job_hash() -> None:
    first = collect_provenance({"case": {"seed": 17}, "schema": "nadit.job/1"})
    second = collect_provenance({"schema": "nadit.job/1", "case": {"seed": 17}})
    changed = collect_provenance({"schema": "nadit.job/1", "case": {"seed": 18}})
    assert first["job_hash"] == second["job_hash"]
    assert len(first["job_hash"]) == 64
    assert first["job_hash"] != changed["job_hash"]
    assert first["python_version"]
    assert first["package_version"]
    assert first["qiskit_version"]


def test_result_snapshots_nested_job_before_collecting_provenance() -> None:
    entry = AtomLayout(())
    result = CompilationResult(AtomProgram(), entry, (), (), ())
    job: dict[str, Any] = {"schema": "nadit.job/1", "case": {"seed": 17}}
    document = encode_result(result, entry_state=entry, job=job)

    job["case"]["seed"] = 18

    assert document["job"]["case"] == {"seed": 17}
    assert (
        document["provenance"]["job_hash"]
        == collect_provenance(document["job"])["job_hash"]
    )


def test_result_snapshots_explicit_nested_metadata() -> None:
    entry = AtomLayout(())
    result = CompilationResult(AtomProgram(), entry, (), (), ())
    provenance = {"versions": {"compiler": "fixture"}}
    metrics = {"load_batch_sizes": [2, 3]}
    validation: dict[str, Any] = {"ok": False, "failure": {"rule": "fixture"}}
    document = encode_result(
        result,
        entry_state=entry,
        job={},
        provenance=provenance,
        metrics=metrics,
        validation=validation,
    )

    provenance["versions"]["compiler"] = "changed"
    metrics["load_batch_sizes"].append(4)
    validation["failure"]["rule"] = "changed"

    assert document["provenance"] == {"versions": {"compiler": "fixture"}}
    assert document["metrics"] == {"load_batch_sizes": [2, 3]}
    assert document["validation"] == {"ok": False, "failure": {"rule": "fixture"}}
