"""Shared action counts and distance-profile timing for compiled programs."""

from collections import Counter
from typing import TypedDict

from .atom_ir import AtomProgram, CZPulse, Load, Move, Store
from .compiler import CompilationResult


class CompilationMetrics(TypedDict):
    """Fixed result-document metrics with microsecond timing units."""

    atom_steps: int
    program_duration_us: float
    duration_us: float
    action_counts: dict[str, int]
    cz_gates_per_pulse: list[int]
    load_batch_sizes: list[int]
    atom_load_count: int
    atom_store_count: int
    max_parallel_atoms: int
    transport_duration_us: float


def program_metrics(
    program: AtomProgram, *, duration_us: float | None = None
) -> CompilationMetrics:
    """Count Atom IR operations using the existing example's definitions.

    Args:
        program: Ordered parallel atom steps.
        duration_us: Optional completion time including pending readout results;
            defaults to the program duration when no results are available.

    Returns:
        The fixed result metric set. ``max_parallel_atoms`` is the largest
        individual Move's target count. ``transport_duration_us`` sums Load,
        Move, and Store action durations, including concurrent actions; program
        duration instead sums the maximum action duration in each step.
    """
    actions = [action for step in program.steps for action in step.actions]
    return {
        "atom_steps": len(program.steps),
        "program_duration_us": program.duration_us,
        "duration_us": program.duration_us if duration_us is None else duration_us,
        "action_counts": dict(Counter(type(action).__name__ for action in actions)),
        "cz_gates_per_pulse": [
            len(action.source_ids) for action in actions if isinstance(action, CZPulse)
        ],
        "load_batch_sizes": [
            len(action.bindings) for action in actions if isinstance(action, Load)
        ],
        "atom_load_count": sum(
            len(action.bindings) for action in actions if isinstance(action, Load)
        ),
        "atom_store_count": sum(
            len(action.bindings) for action in actions if isinstance(action, Store)
        ),
        "max_parallel_atoms": max(
            (len(action.targets) for action in actions if isinstance(action, Move)),
            default=0,
        ),
        "transport_duration_us": sum(
            action.duration_us
            for action in actions
            if isinstance(action, Load | Move | Store)
        ),
    }


def collect_metrics(result: CompilationResult) -> CompilationMetrics:
    """Return the fixed metric set for one completed compilation case.

    Args:
        result: Atom IR, sampled measurement availability, and result records.

    Returns:
        Program metrics whose completion time includes final readout latency.
    """
    return program_metrics(result.program, duration_us=result.duration_us)
