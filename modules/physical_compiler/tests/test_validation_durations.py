"""Device timing and stored metrics cannot understate execution cost."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_job import MODULE, spec

from nadit_physical_compiler.atom_ir import AtomProgram, AtomStep, Move
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.metrics import collect_metrics
from nadit_physical_compiler.validation import validate_result


@pytest.mark.parametrize(
    "kind", ["Load", "Move", "Store", "CZPulse", "Rotation", "Measure", "Reset"]
)
def test_device_duration_tamper(kind: str) -> None:
    job = loads_job(json.dumps(spec()), base_dir=MODULE)
    result = compile_job(job)
    found = False
    steps = []
    for step in result.program.steps:
        actions = []
        for action in step.actions:
            if type(action).__name__ == kind:
                found = True
                action = replace(action, duration_us=0)
            actions.append(action)
        steps.append(AtomStep(tuple(actions)))
    assert found
    changed = replace(result, program=AtomProgram(tuple(steps)))
    report = validate_result(
        changed, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert any(f["rule"] == "duration_mismatch" for f in report.get("failures", []))


def test_stored_metrics_recomputed() -> None:
    job = loads_job(json.dumps(spec()), base_dir=MODULE)
    result = compile_job(job)
    metrics = dict(collect_metrics(result))
    metrics["transport_duration_us"] = 0
    report = validate_result(
        result,
        entry_state=job.entry_state,
        device=job.device,
        dag=job.dag,
        stored_metrics=metrics,
    )
    assert report["failure"] is not None
    assert report["failure"]["rule"] == "metrics_mismatch"


def test_conservative_move_duration_is_allowed() -> None:
    from nadit_physical_compiler.replay import apply_step
    from nadit_physical_compiler.validation_durations import check_duration

    job = loads_job(json.dumps(spec()), base_dir=MODULE)
    layout = job.entry_state
    for step in compile_job(job).program.steps:
        for action in step.actions:
            if isinstance(action, Move):
                assert check_duration(
                    job.device,
                    layout,
                    replace(action, duration_us=action.duration_us + 100),
                ).ok
        layout = apply_step(layout, step, job.device)


@pytest.mark.parametrize("kind", ["global", "u"])
def test_other_pulse_durations(kind: str) -> None:
    from nadit_physical_compiler.atom_ir import GlobalRY, LocalU
    from nadit_physical_compiler.capabilities import GlobalControl
    from nadit_physical_compiler.validation_durations import check_duration

    job = loads_job(json.dumps(spec()), base_dir=MODULE)
    device = replace(job.device, global_control=GlobalControl("global", 3))
    action = (
        GlobalRY("global", device.zones[0].bounds, 0.2, duration_us=0)
        if kind == "global"
        else LocalU("local", (7,), 0.2, 0, 0, duration_us=0)
    )
    assert check_duration(device, job.entry_state, action).rule == "duration_mismatch"


def test_saved_metrics_tamper_rejected_by_cli_and_cost(
    tmp_path: "Path", capsys: pytest.CaptureFixture[str]
) -> None:
    from nadit_physical_compiler.cli import main
    from nadit_physical_compiler.cost import UnvalidatedResult, evaluate
    from nadit_physical_compiler.serialization import encode_result

    job = loads_job(json.dumps(spec()), base_dir=MODULE)
    result = compile_job(job)
    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    document = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
        validation=report,
    )
    document["metrics"]["transport_duration_us"] = 0
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(document))
    assert main(["validate", str(path)]) == 3
    assert json.loads(capsys.readouterr().out)["failure"]["rule"] == "metrics_mismatch"
    with pytest.raises(UnvalidatedResult, match="metrics_mismatch"):
        evaluate(document)
