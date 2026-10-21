"""Independent dependency, feedback, and decision checks over an action timeline."""

from dataclasses import dataclass
from math import isfinite
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
from .compiler import CompilationResult, CompiledOperation, MeasurementRecord
from .device import Device
from .physical_ir import PhysicalDAG, PhysicalOperation, ResultRef

TIME_TOLERANCE_US = 1e-9


class TimingFailure(TypedDict):
    """A violated timing rule at its first contributing action, if present."""

    step: int | None
    action: int | None
    kind: str
    rule: str
    message: str


@dataclass(frozen=True, slots=True)
class _Interval:
    start: float
    end: float
    step: int
    action: int
    kind: str


_KINDS: dict[type[AtomAction], str] = {
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
_METADATA = ("barrier", "global_phase")


def check_timing(
    dag: PhysicalDAG, result: CompilationResult, device: Device
) -> list[TimingFailure]:
    """Check actual action intervals, DAG dependencies, and resolved branches.

    Args:
        dag: Native circuit with stable measurement identities and dependencies.
        result: Actions and their recorded operation decisions and measurements.
        device: Readout latency used to derive the earliest feedback time.

    Returns:
        Failures in step/action order. Causal timing failures precede record
        mismatches at the same action. Whole-result failures have null indices.
        Dependencies pass through barriers and skipped nodes without inventing
        action intervals for those decisions.
    """
    return _TimingChecks(dag, result, device).run()


class _TimingChecks:
    def __init__(self, dag: PhysicalDAG, result: CompilationResult, device: Device):
        self.dag = dag
        self.result = result
        self.latency = device.readout.latency_us if device.readout is not None else 0.0
        self.intervals: dict[int, _Interval] = {}
        self.measurement_ends: dict[ResultRef, float] = {}
        self.records: dict[int, CompiledOperation] = {}
        self.measurements: dict[ResultRef, MeasurementRecord] = {}
        self.failures: list[tuple[int, TimingFailure]] = []

    def run(self) -> list[TimingFailure]:
        self._derive_intervals()
        self._index_records()
        self._dependencies()
        for node in self.dag.nodes:
            self._condition(node)
            self._operation_record(node)
        self._measurement_records()
        return [
            failure
            for _, failure in sorted(
                self.failures,
                key=lambda item: (
                    item[1]["step"] if item[1]["step"] is not None else float("inf"),
                    item[1]["action"]
                    if item[1]["action"] is not None
                    else float("inf"),
                    item[0],
                ),
            )
        ]

    def _add(
        self, source: int | None, rule: str, message: str, priority: int = 0
    ) -> None:
        interval = self.intervals.get(source) if source is not None else None
        self.failures.append(
            (
                priority,
                {
                    "step": interval.step if interval else None,
                    "action": interval.action if interval else None,
                    "kind": interval.kind if interval else "RESULT",
                    "rule": rule,
                    "message": message,
                },
            )
        )

    def _derive_intervals(self) -> None:
        for step_index, (start, step) in enumerate(self.result.program.timeline()):
            for action_index, action in enumerate(step.actions):
                end = start + action.duration_us
                for source in action.source_ids:
                    previous = self.intervals.get(source)
                    self.intervals[source] = (
                        _Interval(
                            previous.start,
                            max(previous.end, end),
                            previous.step,
                            previous.action,
                            previous.kind,
                        )
                        if previous is not None
                        else _Interval(
                            start, end, step_index, action_index, _KINDS[type(action)]
                        )
                    )
                if isinstance(action, Measure):
                    for _, reference in action.results:
                        self.measurement_ends[reference] = max(
                            self.measurement_ends.get(reference, end), end
                        )
        for source in self.intervals:
            if not 0 <= source < len(self.dag.nodes):
                self._add(
                    source,
                    "record_mismatch",
                    f"Action references unknown native operation {source}.",
                    2,
                )

    def _index_records(self) -> None:
        for record in self.result.operations:
            source = record.operation_id
            if source in self.records or not 0 <= source < len(self.dag.nodes):
                self._add(
                    source,
                    "record_mismatch",
                    f"Operation record {source} is duplicated or absent from the DAG.",
                    2,
                )
            self.records[source] = record
        for measurement in self.result.measurements:
            if measurement.result in self.measurements:
                self._add(
                    None,
                    "record_mismatch",
                    f"Measurement {measurement.result.measurement_id} "
                    "has duplicate records.",
                    2,
                )
            self.measurements[measurement.result] = measurement

    def _dependencies(self) -> None:
        predecessors: list[list[int]] = [[] for _ in self.dag.nodes]
        for source, target in self.dag.edges:
            predecessors[target].append(source)
        # A decision has no action interval; carry its executed predecessors
        # through it so barriers and skipped branches preserve wire ordering.
        frontier: list[set[int]] = [set() for _ in self.dag.nodes]
        feedback: list[set[ResultRef]] = [set() for _ in self.dag.nodes]
        for node in self.dag.nodes:
            incoming: set[int] = set()
            conditions: set[ResultRef] = set()
            for source in predecessors[node.id]:
                incoming.update(frontier[source])
                conditions.update(feedback[source])
            if node.condition is not None:
                conditions.add(node.condition.result)
            interval = self.intervals.get(node.id)
            if interval is None:
                frontier[node.id] = incoming
                feedback[node.id] = conditions
                continue
            for source in sorted(incoming):
                previous = self.intervals[source]
                if previous.end > interval.start + TIME_TOLERANCE_US:
                    self._add(
                        node.id,
                        "dependency_order",
                        f"Operation {node.id} starts at {interval.start} us before "
                        f"predecessor {source} ends at {previous.end} us.",
                    )
            for reference in sorted(conditions, key=lambda ref: ref.measurement_id):
                self._feedback(node.id, reference, interval)
            frontier[node.id] = {node.id}

    def _condition(self, node: PhysicalOperation) -> None:
        enabled = True
        if node.condition is not None:
            measurement = self.measurements.get(node.condition.result)
            if measurement is None or measurement.value not in (0, 1):
                self._add(
                    node.id,
                    "condition_mismatch",
                    f"Operation {node.id} has no recorded bit for measurement "
                    f"{node.condition.result.measurement_id}.",
                    1,
                )
                return
            enabled = measurement.value == node.condition.value
        expected = (
            "skipped"
            if not enabled
            else "metadata"
            if node.name in _METADATA
            else "emitted"
        )
        record = self.records.get(node.id)
        if record is None:
            return
        has_actions = node.id in self.intervals
        if record.status != expected or has_actions != (expected == "emitted"):
            self._add(
                node.id,
                "condition_mismatch",
                f"Operation {node.id} requires status {expected!r}; its record is "
                f"{record.status!r} and action presence is {has_actions}.",
                1,
            )

    def _feedback(self, source: int, reference: ResultRef, interval: _Interval) -> None:
        end = self.measurement_ends.get(reference)
        measurement = self.measurements.get(reference)
        if end is None:
            self._add(
                source,
                "feedback_latency",
                f"Operation {source} has no completed Measure action for "
                f"measurement {reference.measurement_id}.",
            )
            return
        available = end + self.latency
        if measurement is not None and isfinite(measurement.available_at_us):
            available = max(available, measurement.available_at_us)
        if interval.start + TIME_TOLERANCE_US < available:
            self._add(
                source,
                "feedback_latency",
                f"Operation {source} starts at {interval.start} us before "
                f"measurement {reference.measurement_id} is available at "
                f"{available} us.",
            )

    def _operation_record(self, node: PhysicalOperation) -> None:
        record = self.records.get(node.id)
        interval = self.intervals.get(node.id)
        if record is None:
            self._add(
                node.id, "record_mismatch", f"Operation {node.id} has no record.", 2
            )
        elif interval is not None and (
            not isfinite(record.start_us)
            or not isfinite(record.end_us)
            or abs(record.start_us - interval.start) > TIME_TOLERANCE_US
            or abs(record.end_us - interval.end) > TIME_TOLERANCE_US
        ):
            self._add(
                node.id,
                "record_mismatch",
                f"Operation {node.id} records [{record.start_us}, {record.end_us}] us; "
                f"its actions span [{interval.start}, {interval.end}] us.",
                2,
            )

    def _measurement_records(self) -> None:
        sources = {
            node.result: node.id for node in self.dag.nodes if node.result is not None
        }
        for reference, end in self.measurement_ends.items():
            record = self.measurements.get(reference)
            ready = end + self.latency
            if record is None:
                message = (
                    f"Measurement {reference.measurement_id} has no result record."
                )
            elif record.value not in (0, 1):
                message = f"Measurement {reference.measurement_id} records a non-bit."
            elif (
                not isfinite(record.available_at_us)
                or record.available_at_us + TIME_TOLERANCE_US < ready
            ):
                message = (
                    f"Measurement {reference.measurement_id} records availability "
                    f"{record.available_at_us} us before its readout completes at "
                    f"{ready} us."
                )
            else:
                continue
            self._add(sources.get(reference), "record_mismatch", message, 2)
        for reference in self.measurements:
            if reference not in self.measurement_ends:
                self._add(
                    sources.get(reference),
                    "record_mismatch",
                    f"Recorded measurement {reference.measurement_id} "
                    "has no Measure action.",
                    2,
                )
