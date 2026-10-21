"""Optional reference checks for a compiled or decoded result document."""

from typing import TypedDict

from .atom_ir import (
    AtomAction,
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
from .compiler import CompilationResult
from .device import Device
from .motion import ConstraintResult, check_load, check_move, check_store
from .replay import apply_step
from .state import AtomLayout, validate_layout


class ValidationFailure(TypedDict):
    """First violated rule, with null indices for whole-result checks."""

    step: int | None
    action: int | None
    kind: str
    rule: str
    message: str


class ValidationReport(TypedDict):
    """JSON-ready outcome of the requested reference checks."""

    ok: bool
    failure: ValidationFailure | None


_ACTION_KINDS: dict[type[AtomAction], str] = {
    Load: "LOAD",
    Move: "MOVE",
    Store: "STORE",
    CZPulse: "CZ_PULSE",
    Rotation: "ROT",
    LocalU: "U",
    GlobalRY: "GLOBAL_RY",
    Reset: "R",
    Measure: "M",
    Wait: "WAIT",
}


def validate_result(
    result: CompilationResult, *, entry_state: AtomLayout, device: Device
) -> ValidationReport:
    """Check transport, replay, and source coverage without compiling again.

    Args:
        result: Compilation output, including its recorded exit layout.
        entry_state: Atom layout before the program's first step.
        device: Device geometry used to interpret the actions.

    Returns:
        A JSON-ready report with ``ok`` and ``failure``. Action indices are
        zero-based. Entry, missing-coverage, and exit-state failures use null
        indices. The first action failure follows program tuple order; each
        action is checked against its unchanged step-entry layout. Metadata
        operations do not require an action source reference.

    This reference tool is independent of the compiler's construction path.
    It uses endpoint transport rules and retained-readout replay; it does not
    simulate quantum states, trajectories, or independently validate pulses.
    """
    try:
        validate_layout(device, entry_state)
    except ValueError as error:
        return _failure(None, None, "ENTRY", "invalid_entry", str(error))

    layout = entry_state
    covered: set[int] = set()
    skipped = {
        operation.operation_id
        for operation in result.operations
        if operation.status == "skipped"
    }
    for step_index, step in enumerate(result.program.steps):
        for action_index, action in enumerate(step.actions):
            checked = _check_action(device, layout, action)
            if checked.rule is not None:
                return _failure(
                    step_index,
                    action_index,
                    _ACTION_KINDS[type(action)],
                    checked.rule,
                    checked.message,
                )
            for source in action.source_ids:
                if source in skipped:
                    return _failure(
                        step_index,
                        action_index,
                        _ACTION_KINDS[type(action)],
                        "skipped_source",
                        f"Skipped operation {source} appears in action source_ids.",
                    )
                covered.add(source)
        layout = apply_step(layout, step, device)

    for operation in result.operations:
        if operation.status == "emitted" and operation.operation_id not in covered:
            return _failure(
                None,
                None,
                "COVERAGE",
                "missing_source",
                f"Emitted operation {operation.operation_id} has no action "
                "source reference.",
            )
    if layout != result.exit_state:
        return _failure(
            None,
            None,
            "RESULT",
            "exit_state_mismatch",
            "Replayed layout does not equal the recorded exit state.",
        )
    return {"ok": True, "failure": None}


def _check_action(
    device: Device, layout: AtomLayout, action: AtomAction
) -> ConstraintResult:
    if isinstance(action, Load):
        return check_load(
            device,
            layout,
            action.grid,
            {atom: (row, column) for atom, row, column in action.bindings},
        )
    if isinstance(action, Move):
        return check_move(device, layout, action.grid, dict(action.targets))
    if isinstance(action, Store):
        return check_store(device, layout, action.grid, dict(action.bindings))
    if isinstance(action, Measure) and not action.retain_atoms:
        return ConstraintResult(
            "unsupported_replay", "Replay currently supports retained readout only."
        )
    return ConstraintResult()


def _failure(
    step: int | None,
    action: int | None,
    kind: str,
    rule: str,
    message: str,
) -> ValidationReport:
    return {
        "ok": False,
        "failure": {
            "step": step,
            "action": action,
            "kind": kind,
            "rule": rule,
            "message": message,
        },
    }
