"""Shared metrics retain the examples' action-counting and timing semantics."""

from nadit_physical_compiler.atom_ir import (
    AtomProgram,
    AtomStep,
    CZPulse,
    Load,
    Measure,
    Move,
    Store,
    Wait,
)
from nadit_physical_compiler.compiler import CompilationResult, MeasurementRecord
from nadit_physical_compiler.device import Position
from nadit_physical_compiler.metrics import collect_metrics, program_metrics
from nadit_physical_compiler.physical_ir import ResultRef
from nadit_physical_compiler.serialization import encode_result
from nadit_physical_compiler.state import AODGrid, AtomLayout
from reference.pipeline_candidate import _metrics


def test_empty_program_metrics() -> None:
    assert program_metrics(AtomProgram()) == {
        "atom_steps": 0,
        "program_duration_us": 0.0,
        "duration_us": 0.0,
        "action_counts": {},
        "cz_gates_per_pulse": [],
        "load_batch_sizes": [],
        "atom_load_count": 0,
        "atom_store_count": 0,
        "max_parallel_atoms": 0,
        "transport_duration_us": 0,
    }


def _parallel_program() -> AtomProgram:
    first, second = AODGrid("first"), AODGrid("second")
    return AtomProgram(
        (
            AtomStep((Wait(duration_us=3),)),
            AtomStep(
                (
                    Load(first, ((0, 0, 0), (1, 0, 1)), duration_us=2),
                    Load(second, ((2, 0, 0),), duration_us=4),
                )
            ),
            AtomStep(
                (
                    Move(
                        first, ((0, Position(1, 2)), (1, Position(3, 4))), duration_us=5
                    ),
                    Move(second, ((2, Position(5, 6)),), duration_us=6),
                )
            ),
            AtomStep(
                (
                    Store(first, ((0, "a"), (1, "b")), duration_us=2),
                    Store(second, ((2, "c"),), duration_us=3),
                )
            ),
            AtomStep((CZPulse("cz", (0, 1, 2, 3), duration_us=1, source_ids=(5, 6)),)),
        )
    )


def test_parallel_actions_keep_original_example_aggregation() -> None:
    assert program_metrics(_parallel_program()) == {
        "atom_steps": 5,
        "program_duration_us": 17,
        "duration_us": 17,
        "action_counts": {"Wait": 1, "Load": 2, "Move": 2, "Store": 2, "CZPulse": 1},
        "cz_gates_per_pulse": [2],
        "load_batch_sizes": [2, 1],
        "atom_load_count": 3,
        "atom_store_count": 3,
        "max_parallel_atoms": 2,
        "transport_duration_us": 22,
    }


def test_measurement_completion_uses_recorded_latency() -> None:
    program = AtomProgram(
        (AtomStep((Measure("camera", ((0, ResultRef(7)),), True, duration_us=10),)),)
    )
    result = CompilationResult(
        program, AtomLayout(()), (), (MeasurementRecord(ResultRef(7), 1, 510),), ()
    )
    metrics = collect_metrics(result)
    assert metrics == {
        "atom_steps": 1,
        "program_duration_us": 10,
        "duration_us": 510,
        "action_counts": {"Measure": 1},
        "cz_gates_per_pulse": [],
        "load_batch_sizes": [],
        "atom_load_count": 0,
        "atom_store_count": 0,
        "max_parallel_atoms": 0,
        "transport_duration_us": 0,
    }
    document = encode_result(result, entry_state=AtomLayout(()), job={}, provenance={})
    assert document["metrics"] == metrics


def test_program_can_supply_completion_time() -> None:
    assert program_metrics(AtomProgram(), duration_us=23)["duration_us"] == 23


def test_reference_metric_aliases_are_unchanged() -> None:
    assert _metrics(_parallel_program(), 0.25) == {
        "load_actions": 2,
        "store_actions": 2,
        "loaded_atoms": 3,
        "stored_atoms": 3,
        "move_actions": 2,
        "movement_us": 11,
        "transfer_us": 11,
        "rearrangement_us": 22,
        "total_us": 17,
        "compilation_s": 0.25,
    }
