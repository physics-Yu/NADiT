"""Semantic report integration and the two saved-result counterexamples."""

from dataclasses import replace
from pathlib import Path

import pytest

from examples.viewer_cases import compile_feedback, compile_static
from nadit_physical_compiler import (
    AtomProgram,
    AtomStep,
    CZPulse,
    Position,
    Region,
    Wait,
)
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.validation import validate_result


@pytest.mark.parametrize("bit", [0, 1])
def test_feedback_semantics_and_report_groups(bit: int) -> None:
    example = compile_feedback(bit)
    report = validate_result(
        example.result,
        entry_state=example.entry,
        device=example.device,
        dag=example.dag,
    )
    assert report == {
        "ok": True,
        "failure": None,
        "failures": [],
        "failure_count": 0,
        "checks": ["motion", "coverage", "replay", "pulse", "addressing", "timing"],
        "semantic": "checked",
    }


def test_static_compiled_example_passes_semantic_checks() -> None:
    example = compile_static()
    report = validate_result(
        example.result,
        entry_state=example.entry,
        device=example.device,
        dag=example.dag,
    )
    assert report["ok"], report


@pytest.mark.parametrize("transport", ["sequential", "strict", "staged", "relaxed"])
@pytest.mark.parametrize("bit", [0, 1])
def test_saved_job_transports_and_measurement_branches_pass(
    transport: str, bit: int
) -> None:
    import json

    module = Path(__file__).parents[1]
    document = load_job(module / "examples/jobs/multipair.json").document
    document["strategy"]["placement"] = {"kind": "ids"}
    document["strategy"]["transport"] = {"kind": transport}
    document["strategy"]["reuse"] = {"kind": "matching"}
    document["case"]["overrides"] = {"12": bit}
    job = loads_job(json.dumps(document), base_dir=module)
    report = validate_result(
        compile_job(job), entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"], report


def test_empty_cz_illumination_reports_missing_interaction() -> None:
    example = compile_feedback(1)
    steps = tuple(
        AtomStep(
            tuple(
                replace(action, coverage=Region(-100, -100, -90, -90))
                if isinstance(action, CZPulse)
                else action
                for action in step.actions
            )
        )
        for step in example.result.program.steps
    )
    result = replace(
        example.result, program=replace(example.result.program, steps=steps)
    )
    report = validate_result(
        result, entry_state=example.entry, device=example.device, dag=example.dag
    )
    assert report["failure"] is not None
    assert report["failure"]["rule"] == "missing_interaction"
    assert report["failure_count"] >= 3
    assert "timing" in report["checks"]


@pytest.mark.parametrize("bit", [0, 1])
def test_removed_feedback_wait_reports_latency_before_stale_records(bit: int) -> None:
    example = compile_feedback(bit)
    steps = tuple(
        step
        for step in example.result.program.steps
        if not all(isinstance(action, Wait) for action in step.actions)
    )
    assert len(steps) < len(example.result.program.steps)
    result = replace(
        example.result, program=replace(example.result.program, steps=steps)
    )
    report = validate_result(
        result, entry_state=example.entry, device=example.device, dag=example.dag
    )
    assert report["failure"] is not None
    assert report["failure"]["rule"] == "feedback_latency"
    assert "record_mismatch" in {item["rule"] for item in report["failures"]}


def test_detailed_legacy_report_explicitly_lacks_semantic_evidence() -> None:
    example = compile_feedback(1)
    report = validate_result(
        example.result, entry_state=example.entry, device=example.device, detailed=True
    )
    assert report == {
        "ok": True,
        "failure": None,
        "failures": [],
        "failure_count": 0,
        "checks": ["motion", "coverage", "replay"],
        "semantic": "unavailable",
    }


def test_report_caps_details_but_counts_all_failures() -> None:
    example = compile_feedback(1)
    original = example.result.program
    steps = list(original.steps)
    for _ in range(30):
        steps.append(
            AtomStep(
                (
                    CZPulse(
                        "cz",
                        Region(-100, -100, -90, -90),
                        duration_us=1,
                        source_ids=(4,),
                    ),
                )
            )
        )
    result = replace(
        example.result, program=AtomProgram(tuple(steps), original.global_phase)
    )
    report = validate_result(
        result, entry_state=example.entry, device=example.device, dag=example.dag
    )
    assert not report["ok"]
    assert len(report["failures"]) == 20
    assert report["failure_count"] > 20
    assert report["failure"] == report["failures"][0]


def test_transport_or_wait_provenance_cannot_replace_gate_execution() -> None:
    example = compile_feedback(1)
    steps = tuple(
        AtomStep(
            tuple(
                Wait(duration_us=action.duration_us, source_ids=action.source_ids)
                if isinstance(action, CZPulse)
                else action
                for action in step.actions
            )
        )
        for step in example.result.program.steps
    )
    result = replace(
        example.result, program=replace(example.result.program, steps=steps)
    )
    report = validate_result(
        result, entry_state=example.entry, device=example.device, dag=example.dag
    )
    assert not report["ok"]
    assert any(item["rule"] == "addressing" for item in report["failures"])


@pytest.mark.parametrize("kind", ["Rotation", "CZPulse"])
def test_native_operation_cannot_execute_twice_in_parallel(kind: str) -> None:
    example = compile_feedback(1)
    steps = list(example.result.program.steps)
    for index, step in enumerate(steps):
        duplicate = next(
            (action for action in step.actions if type(action).__name__ == kind), None
        )
        if duplicate is not None:
            steps[index] = AtomStep((*step.actions, replace(duplicate)))
            break
    result = replace(
        example.result, program=replace(example.result.program, steps=tuple(steps))
    )
    report = validate_result(
        result, entry_state=example.entry, device=example.device, dag=example.dag
    )
    assert not report["ok"]
    assert any(
        item["rule"] == "addressing" and "more than once" in item["message"]
        for item in report["failures"]
    )


def test_invalid_entry_keeps_independent_checks_and_reports_only_run_groups() -> None:
    example = compile_feedback(1)
    first = example.entry.atoms[0]
    entry = replace(
        example.entry,
        atoms=(
            replace(first, position=Position(first.position.x + 1, first.position.y)),
            *example.entry.atoms[1:],
        ),
    )
    report = validate_result(
        example.result, entry_state=entry, device=example.device, dag=example.dag
    )
    assert report["failure"] is not None
    assert report["failure"]["rule"] == "invalid_entry"
    assert report["checks"] == ["motion", "coverage", "timing"]
