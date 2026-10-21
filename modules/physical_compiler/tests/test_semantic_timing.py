"""Timing validation derives execution from actions and stable result identities."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from examples.viewer_cases import compile_feedback
from nadit_physical_compiler import (
    AtomLayout,
    AtomProgram,
    AtomStep,
    CompilationResult,
    CompiledOperation,
    Condition,
    Measure,
    MeasurementRecord,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
    Rotation,
    Wait,
    load_device,
)
from nadit_physical_compiler.validation_timing import check_timing

MODULE = Path(__file__).resolve().parents[1]
DEVICE = load_device(MODULE / "configs/multipair_flexible.json")


def _rotation(source: int, duration: float = 1.0) -> Rotation:
    return Rotation("raman", (7,), "X", 0.5, duration_us=duration, source_ids=(source,))


def _result(
    steps: tuple[AtomStep, ...],
    operations: tuple[CompiledOperation, ...],
    measurements: tuple[MeasurementRecord, ...] = (),
    phase: float = 0.0,
) -> CompilationResult:
    return CompilationResult(
        AtomProgram(steps, phase), AtomLayout(()), operations, measurements, ()
    )


def _two_rotations() -> PhysicalDAG:
    return PhysicalDAG(
        1,
        0,
        (
            PhysicalOperation(0, "rx", (0,), (), (0.5,)),
            PhysicalOperation(1, "rx", (0,), (), (0.5,)),
        ),
        ((0, 1),),
    )


def _feedback(bit: int = 1) -> tuple[PhysicalDAG, CompilationResult]:
    reference = ResultRef(19)
    dag = PhysicalDAG(
        1,
        1,
        (
            PhysicalOperation(0, "measure", (0,), (0,), (), source_id=19),
            PhysicalOperation(1, "rx", (0,), (), (0.5,), Condition(reference)),
        ),
        ((0, 1),),
    )
    assert DEVICE.readout is not None
    duration = DEVICE.readout.duration_us
    ready = duration + DEVICE.readout.latency_us
    steps: tuple[AtomStep, ...] = (
        AtomStep(
            (
                Measure(
                    "readout",
                    ((7, reference),),
                    True,
                    duration_us=duration,
                    source_ids=(0,),
                ),
            )
        ),
        AtomStep((Wait(duration_us=DEVICE.readout.latency_us),)),
    )
    if bit:
        steps += (AtomStep((_rotation(1),)),)
    result = _result(
        steps,
        (
            CompiledOperation(0, "emitted", 0, duration),
            CompiledOperation(1, "emitted" if bit else "skipped", ready, ready + bit),
        ),
        (MeasurementRecord(reference, bit, ready),),
    )
    return dag, result


@pytest.mark.parametrize("bit", [0, 1])
def test_compiled_feedback_branches_pass(bit: int) -> None:
    example = compile_feedback(bit)
    assert check_timing(example.dag, example.result, example.device) == []


@pytest.mark.parametrize("bit", [0, 1])
def test_stable_source_measurement_identity_and_skipped_branch(bit: int) -> None:
    dag, result = _feedback(bit)
    assert check_timing(dag, result, DEVICE) == []


def test_removed_feedback_wait_reports_latency_before_stale_records() -> None:
    example = compile_feedback(1)
    steps = tuple(
        step
        for step in example.result.program.steps
        if not all(isinstance(action, Wait) for action in step.actions)
    )
    damaged = replace(
        example.result, program=replace(example.result.program, steps=steps)
    )

    failures = check_timing(example.dag, damaged, example.device)

    assert failures[0]["rule"] == "feedback_latency"
    assert any(failure["rule"] == "record_mismatch" for failure in failures)


def test_forged_early_measurement_record_does_not_remove_hardware_latency() -> None:
    dag, result = _feedback()
    assert DEVICE.readout is not None
    end = DEVICE.readout.duration_us
    damaged = replace(
        result,
        program=AtomProgram((result.program.steps[0], result.program.steps[-1])),
        operations=(
            result.operations[0],
            CompiledOperation(1, "emitted", end, end + 1),
        ),
        measurements=(replace(result.measurements[0], available_at_us=end),),
    )

    assert any(
        item["rule"] == "feedback_latency"
        for item in check_timing(dag, damaged, DEVICE)
    )


def test_later_recorded_availability_is_respected() -> None:
    dag, result = _feedback()
    damaged = replace(
        result,
        measurements=(
            replace(result.measurements[0], available_at_us=result.duration_us + 10),
        ),
    )
    assert any(
        item["rule"] == "feedback_latency"
        for item in check_timing(dag, damaged, DEVICE)
    )


def test_reordered_dependency_is_detected_even_when_records_match() -> None:
    dag = _two_rotations()
    result = _result(
        (AtomStep((_rotation(1),)), AtomStep((_rotation(0),))),
        (CompiledOperation(0, "emitted", 1, 2), CompiledOperation(1, "emitted", 0, 1)),
    )
    failures = check_timing(dag, result, DEVICE)
    assert failures[0]["rule"] == "dependency_order"
    assert failures[0]["step"] == 0
    assert failures[0]["action"] == 0
    assert failures[0]["kind"] == "ROT"


def test_same_step_dependency_uses_individual_action_end() -> None:
    result = _result(
        (AtomStep((_rotation(0, 2), _rotation(1))),),
        (CompiledOperation(0, "emitted", 0, 2), CompiledOperation(1, "emitted", 0, 1)),
    )
    failures = check_timing(_two_rotations(), result, DEVICE)
    assert failures[0]["rule"] == "dependency_order"
    assert failures[0]["action"] == 1


def test_recorded_interval_spans_all_actions_without_step_padding() -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    result = _result(
        (
            AtomStep((_rotation(0, 2), Wait(duration_us=5))),
            AtomStep((_rotation(0, 1), Wait(duration_us=3))),
        ),
        (CompiledOperation(0, "emitted", 0, 6),),
    )
    assert check_timing(dag, result, DEVICE) == []

    damaged = replace(result, operations=(CompiledOperation(0, "emitted", 0, 8),))
    assert check_timing(dag, damaged, DEVICE)[0]["rule"] == "record_mismatch"


@pytest.mark.parametrize("offset", [0, 5e-10])
def test_recorded_time_tolerance(offset: float) -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    result = _result(
        (AtomStep((_rotation(0),)),),
        (CompiledOperation(0, "emitted", offset, 1 + offset),),
    )
    assert check_timing(dag, result, DEVICE) == []


def test_false_condition_cannot_be_emitted() -> None:
    dag, result = _feedback()
    damaged = replace(result, measurements=(replace(result.measurements[0], value=0),))
    assert any(
        item["rule"] == "condition_mismatch"
        for item in check_timing(dag, damaged, DEVICE)
    )


def test_true_condition_cannot_be_skipped() -> None:
    dag, result = _feedback(0)
    damaged = replace(result, measurements=(replace(result.measurements[0], value=1),))
    assert any(
        item["rule"] == "condition_mismatch"
        for item in check_timing(dag, damaged, DEVICE)
    )


def test_false_condition_with_skipped_record_still_cannot_have_actions() -> None:
    dag, result = _feedback()
    damaged = replace(
        result,
        measurements=(replace(result.measurements[0], value=0),),
        operations=(
            result.operations[0],
            replace(result.operations[1], status="skipped"),
        ),
    )
    assert any(
        item["rule"] == "condition_mismatch"
        for item in check_timing(dag, damaged, DEVICE)
    )


def test_missing_condition_result_is_reported() -> None:
    dag, result = _feedback()
    assert any(
        item["rule"] == "condition_mismatch"
        for item in check_timing(dag, replace(result, measurements=()), DEVICE)
    )


@pytest.mark.parametrize("status", ["skipped", "metadata"])
def test_unconditional_gate_requires_emitted_status(
    status: Literal["skipped", "metadata"],
) -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    record = replace(CompiledOperation(0, "emitted", 0, 1), status=status)
    result = _result((), (record,))
    assert check_timing(dag, result, DEVICE)[0]["rule"] == "condition_mismatch"


@pytest.mark.parametrize("name,params", [("barrier", ()), ("global_phase", (0.25,))])
def test_enabled_metadata_requires_no_action(
    name: str, params: tuple[float, ...]
) -> None:
    dag = PhysicalDAG(
        1, 0, (PhysicalOperation(0, name, (0,), (), params),), (), global_phase=0.5
    )
    result = _result(
        (), (CompiledOperation(0, "metadata", 0, 0),), phase=0.5 + sum(params)
    )
    assert check_timing(dag, result, DEVICE) == []


def test_unknown_action_source_is_reported() -> None:
    result = _result((AtomStep((_rotation(99),)),), ())
    failures = check_timing(PhysicalDAG(1, 0, (), ()), result, DEVICE)
    assert failures[0]["rule"] == "record_mismatch"
    assert failures[0]["step"] == 0


def test_missing_operation_record_is_reported() -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    result = _result((AtomStep((_rotation(0),)),), ())
    assert check_timing(dag, result, DEVICE)[0]["rule"] == "record_mismatch"


def test_empty_dag_and_program_pass() -> None:
    assert check_timing(PhysicalDAG(0, 0, (), ()), _result((), ()), DEVICE) == []


def test_dependency_order_passes_through_barrier_metadata() -> None:
    dag = PhysicalDAG(
        1,
        0,
        (
            PhysicalOperation(0, "rx", (0,), (), (0.5,)),
            PhysicalOperation(1, "barrier", (0,), (), ()),
            PhysicalOperation(2, "rx", (0,), (), (0.5,)),
        ),
        ((0, 1), (1, 2)),
    )
    damaged = _result(
        (AtomStep((_rotation(2),)), AtomStep((_rotation(0),))),
        (
            CompiledOperation(0, "emitted", 1, 2),
            CompiledOperation(1, "metadata", 2, 2),
            CompiledOperation(2, "emitted", 0, 1),
        ),
    )
    assert check_timing(dag, damaged, DEVICE)[0]["rule"] == "dependency_order"


@pytest.mark.parametrize("name,params", [("barrier", ()), ("global_phase", (0.25,))])
@pytest.mark.parametrize("bit", [0, 1])
def test_conditional_metadata_follows_the_recorded_bit(
    name: str, params: tuple[float, ...], bit: int
) -> None:
    dag, result = _feedback(bit)
    metadata = replace(dag.nodes[1], name=name, params=params)
    dag = replace(dag, nodes=(dag.nodes[0], metadata))
    decision_time = result.measurements[0].available_at_us
    result = replace(
        result,
        program=AtomProgram(result.program.steps[:2], sum(params) if bit else 0),
        operations=(
            result.operations[0],
            CompiledOperation(
                1, "metadata" if bit else "skipped", decision_time, decision_time
            ),
        ),
    )
    assert check_timing(dag, result, DEVICE) == []


def test_dependency_order_passes_through_a_skipped_operation() -> None:
    dag, result = _feedback(0)
    last = PhysicalOperation(2, "rx", (0,), (), (0.5,))
    dag = replace(dag, nodes=(*dag.nodes, last), edges=(*dag.edges, (1, 2)))
    damaged = replace(
        result,
        program=AtomProgram((AtomStep((_rotation(2),)), *result.program.steps)),
        operations=(
            replace(
                result.operations[0], start_us=1, end_us=result.operations[0].end_us + 1
            ),
            result.operations[1],
            CompiledOperation(2, "emitted", 0, 1),
        ),
    )
    assert check_timing(dag, damaged, DEVICE)[0]["rule"] == "dependency_order"


def test_duplicate_and_unknown_operation_records_are_reported() -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    record = CompiledOperation(0, "emitted", 0, 1)
    result = _result(
        (AtomStep((_rotation(0),)),),
        (record, record, CompiledOperation(99, "skipped", 1, 1)),
    )
    failures = check_timing(dag, result, DEVICE)
    assert len(failures) == 2
    assert {failure["rule"] for failure in failures} == {"record_mismatch"}


def test_nonfinite_recorded_interval_fails() -> None:
    dag = PhysicalDAG(1, 0, (_two_rotations().nodes[0],), ())
    result = _result(
        (AtomStep((_rotation(0),)),),
        (CompiledOperation(0, "emitted", float("nan"), 1),),
    )
    assert check_timing(dag, result, DEVICE)[0]["rule"] == "record_mismatch"


def test_feedback_latency_passes_through_skipped_operation_to_successor() -> None:
    dag, result = _feedback(0)
    last = PhysicalOperation(2, "rx", (0,), (), (0.5,))
    dag = replace(dag, nodes=(*dag.nodes, last), edges=(*dag.edges, (1, 2)))
    measurement_end = result.operations[0].end_us
    damaged = replace(
        result,
        program=AtomProgram((result.program.steps[0], AtomStep((_rotation(2),)))),
        operations=(
            *result.operations,
            CompiledOperation(2, "emitted", measurement_end, measurement_end + 1),
        ),
    )

    failures = check_timing(dag, damaged, DEVICE)

    assert failures[0]["rule"] == "feedback_latency"
    assert failures[0]["step"] == 1


def test_feedback_latency_passes_through_conditional_metadata() -> None:
    dag, result = _feedback(1)
    barrier = replace(dag.nodes[1], name="barrier", params=())
    last = PhysicalOperation(2, "rx", (0,), (), (0.5,))
    dag = replace(dag, nodes=(dag.nodes[0], barrier, last), edges=(*dag.edges, (1, 2)))
    measurement_end = result.operations[0].end_us
    decision_time = result.measurements[0].available_at_us
    damaged = replace(
        result,
        program=AtomProgram((result.program.steps[0], AtomStep((_rotation(2),)))),
        operations=(
            result.operations[0],
            CompiledOperation(1, "metadata", decision_time, decision_time),
            CompiledOperation(2, "emitted", measurement_end, measurement_end + 1),
        ),
    )
    assert check_timing(dag, damaged, DEVICE)[0]["rule"] == "feedback_latency"


@pytest.mark.parametrize("bit", [0, 1])
def test_removed_wait_is_detected_for_both_compiled_branches(bit: int) -> None:
    example = compile_feedback(bit)
    damaged = replace(
        example.result,
        program=replace(
            example.result.program,
            steps=tuple(
                step
                for step in example.result.program.steps
                if not all(isinstance(action, Wait) for action in step.actions)
            ),
        ),
    )
    assert (
        check_timing(example.dag, damaged, example.device)[0]["rule"]
        == "feedback_latency"
    )


def test_measurement_record_cannot_understate_availability_without_consumer() -> None:
    dag, result = _feedback(0)
    dag = replace(dag, nodes=dag.nodes[:1], edges=())
    damaged = replace(
        result,
        program=AtomProgram(result.program.steps[:1]),
        operations=result.operations[:1],
        measurements=(replace(result.measurements[0], available_at_us=0),),
    )
    assert check_timing(dag, damaged, DEVICE)[0]["rule"] == "record_mismatch"


@pytest.mark.parametrize("records", ["missing", "duplicate", "nonbit"])
def test_measurement_record_shape_errors_are_reported(records: str) -> None:
    dag, result = _feedback(0)
    measurement = result.measurements[0]
    malformed = {
        "missing": (),
        "duplicate": (measurement, measurement),
        "nonbit": (replace(measurement, value=3),),
    }
    damaged = replace(result, measurements=malformed[records])
    assert any(
        failure["rule"] == "record_mismatch"
        for failure in check_timing(dag, damaged, DEVICE)
    )
