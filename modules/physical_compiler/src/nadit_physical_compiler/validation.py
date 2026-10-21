"""Optional reference checks for a compiled or decoded result document."""

from collections import defaultdict
from typing import NotRequired, TypedDict

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
from .physical_ir import PhysicalDAG
from .replay import apply_step
from .semantic_actions import check_action_semantics
from .state import AtomLayout, validate_layout
from .validation_timing import TIME_TOLERANCE_US as TIME_TOLERANCE_US
from .validation_timing import check_timing


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
    failures: NotRequired[list[ValidationFailure]]
    failure_count: NotRequired[int]
    checks: NotRequired[list[str]]
    semantic: NotRequired[str]
    device_override: NotRequired[str]


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
    result: CompilationResult,
    *,
    entry_state: AtomLayout,
    device: Device,
    dag: PhysicalDAG | None = None,
    detailed: bool = False,
) -> ValidationReport:
    """Check saved actions independently of compiler construction.

    Args:
        result: Decoded actions, records, and expected exit layout.
        entry_state: Layout before the first step.
        device: Embedded Device snapshot, or an explicitly chosen replacement.
        dag: Native circuit for pulse, addressing, timing, and branch checks.
        detailed: Include all-check reporting for a legacy result without a DAG.

    Returns:
        With a DAG, a semantic report containing at most 20 failure details,
        the full failure count, and the executed check groups. Without a DAG,
        detailed reports mark semantics unavailable; the default retains the
        legacy compact structural report. Checks use common step-entry layouts.
        If replay cannot produce a state, independent timing and coverage checks
        continue; subsequent spatial checks cannot use an invented layout.
    """
    if dag is None and not detailed:
        return _validate_legacy(result, entry_state=entry_state, device=device)
    return _validate_detailed(result, entry_state, device, dag)


def _validate_legacy(
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


class _Failures:
    def __init__(self) -> None:
        self.items: list[ValidationFailure] = []
        self.count = 0

    def add(self, failure: ValidationFailure) -> None:
        self.count += 1
        if len(self.items) < 20:
            self.items.append(failure)

    def record(
        self,
        step: int | None,
        action: int | None,
        kind: str,
        rule: str,
        message: str,
    ) -> None:
        self.add(
            {
                "step": step,
                "action": action,
                "kind": kind,
                "rule": rule,
                "message": message,
            }
        )


def _validate_detailed(
    result: CompilationResult,
    entry: AtomLayout,
    device: Device,
    dag: PhysicalDAG | None,
) -> ValidationReport:
    failures = _Failures()
    layout: AtomLayout | None = entry
    try:
        validate_layout(device, entry)
    except ValueError as error:
        failures.record(None, None, "ENTRY", "invalid_entry", str(error))
        layout = None
    spatial_checks_ran = layout is not None
    if dag is not None and len(entry.qubit_to_atom) != dag.num_qubits:
        failures.record(
            None,
            None,
            "ENTRY",
            "addressing",
            "Entry state must bind every physical qubit to an atom.",
        )

    timed: dict[tuple[int | None, int | None], list[ValidationFailure]] = defaultdict(
        list
    )
    if dag is not None:
        for failure in check_timing(dag, result, device):
            timed[failure["step"], failure["action"]].append(failure)
    covered: set[int] = set()
    executed: set[int] = set()
    skipped = {
        item.operation_id for item in result.operations if item.status == "skipped"
    }

    for step_index, step in enumerate(result.program.steps):
        for action_index, action in enumerate(step.actions):
            kind = _ACTION_KINDS[type(action)]
            if layout is not None:
                checked = _check_action(device, layout, action)
                if checked.rule is not None:
                    failures.record(
                        step_index, action_index, kind, checked.rule, checked.message
                    )
                if dag is not None:
                    for checked in check_action_semantics(
                        dag, device, layout, action, step
                    ):
                        if checked.rule is not None:
                            failures.record(
                                step_index,
                                action_index,
                                kind,
                                checked.rule,
                                checked.message,
                            )
            if isinstance(
                action, CZPulse | Rotation | LocalU | GlobalRY | Reset | Measure
            ):
                for source in action.source_ids:
                    if source in executed:
                        failures.record(
                            step_index,
                            action_index,
                            kind,
                            "addressing",
                            f"Native operation {source} is executed more than once.",
                        )
                    executed.add(source)
            for failure in timed.pop((step_index, action_index), ()):
                failures.add(failure)
            for source in action.source_ids:
                if source in skipped:
                    failures.record(
                        step_index,
                        action_index,
                        kind,
                        "skipped_source",
                        f"Skipped operation {source} appears in action source_ids.",
                    )
                covered.add(source)
        if layout is not None:
            try:
                layout = apply_step(layout, step, device)
            except (KeyError, ValueError, NotImplementedError) as error:
                failures.record(step_index, None, "REPLAY", "replay_error", str(error))
                layout = None
        for failure in timed.pop((step_index, None), ()):
            failures.add(failure)

    for operation in result.operations:
        if operation.status != "emitted":
            continue
        if operation.operation_id not in covered:
            failures.record(
                None,
                None,
                "COVERAGE",
                "missing_source",
                f"Emitted operation {operation.operation_id} has no action "
                "source reference.",
            )
        elif dag is not None and operation.operation_id not in executed:
            failures.record(
                None,
                None,
                "COVERAGE",
                "addressing",
                f"Emitted operation {operation.operation_id} has no executable "
                "gate action.",
            )
    for remaining in timed.values():
        for failure in remaining:
            failures.add(failure)
    if layout is not None and layout != result.exit_state:
        failures.record(
            None,
            None,
            "RESULT",
            "exit_state_mismatch",
            "Replayed layout does not equal the recorded exit state.",
        )
    checks = ["motion", "coverage"]
    if spatial_checks_ran:
        checks.append("replay")
    if dag is not None:
        if spatial_checks_ran:
            checks += ["pulse", "addressing"]
        checks.append("timing")
    return {
        "ok": failures.count == 0,
        "failure": failures.items[0] if failures.items else None,
        "failures": failures.items,
        "failure_count": failures.count,
        "checks": checks,
        "semantic": "checked" if dag is not None else "unavailable",
    }


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
