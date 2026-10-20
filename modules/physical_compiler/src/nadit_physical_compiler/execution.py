"""Dependency-only execution of physical circuits with fake measurement feedback."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from heapq import heappop, heappush
from math import isfinite
from typing import Literal, Protocol

from .ir import PhysicalDAG, ResultRef


class MeasurementBackend(Protocol):
    """Supply a measurement bit when the execution makes its result available."""

    def measure(self, result: ResultRef) -> int:
        """Return the bit for a particular measurement occurrence."""
        ...


class FakeBackend:
    """Generate reproducible test bits independently of execution order.

    Args:
        seed: Seed combined with each measurement's operation ID.
        overrides: Explicit bits for selected measurement occurrences.

    This backend does not simulate quantum states or measurement probabilities.
    """

    def __init__(
        self, seed: int = 0, overrides: Mapping[ResultRef, int] | None = None
    ) -> None:
        self._seed = seed
        self._overrides = dict(overrides or {})

    def measure(self, result: ResultRef) -> int:
        """Return an explicit override or a stable seeded bit for ``result``."""
        if result in self._overrides:
            return self._overrides[result]
        key = f"{self._seed}:{result.measurement_id}".encode("ascii")
        return sha256(key).digest()[0] & 1


@dataclass(frozen=True, slots=True)
class OperationEvent:
    """An operation's ID, branch decision, and abstract start/end times."""

    operation_id: int
    status: Literal["executed", "skipped"]
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class MeasurementEvent:
    """A measurement occurrence, its bit, and when that bit became available."""

    result: ResultRef
    value: int
    available_at: float


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    """Operations in ID order and measurements in result availability order."""

    operations: tuple[OperationEvent, ...]
    measurements: tuple[MeasurementEvent, ...]

    @property
    def duration(self) -> float:
        """Return the final operation or measurement-report completion time."""
        return max(
            max((event.end for event in self.operations), default=0.0),
            max((event.available_at for event in self.measurements), default=0.0),
        )


def execute(
    dag: PhysicalDAG,
    backend: MeasurementBackend | None = None,
    *,
    durations: Mapping[str, float] | None = None,
    measurement_latency: float = 0.0,
) -> ExecutionTrace:
    """Execute DAG dependencies and conditions using fresh per-run state.

    Args:
        dag: Physical operations with wire dependencies and result references.
        backend: Measurement provider; defaults to ``FakeBackend(seed=0)``.
        durations: Abstract durations by operation name. Unspecified operations
            take one unit; barriers and global-phase operations take zero.
        measurement_latency: Time from measurement completion to result delivery.

    Returns:
        An immutable trace of executed/skipped operations and available results.

    Raises:
        ValueError: If a time is negative/nonfinite or a backend returns a non-bit.

    Operations run as soon as their DAG dependencies and conditions permit.
    This is a feedback test executor, without hardware resources or atom actions.
    """
    operation_durations = {"barrier": 0.0, "global_phase": 0.0}
    operation_durations.update(durations or {})
    for name, duration in operation_durations.items():
        _check_time(duration, f"Operation {name!r} duration")
    _check_time(measurement_latency, "Measurement latency")
    runner = _Execution(
        dag,
        backend if backend is not None else FakeBackend(),
        operation_durations,
        measurement_latency,
    )
    return runner.run()


def _check_time(value: float, context: str) -> None:
    if not isfinite(value) or value < 0:
        raise ValueError(f"{context} must be nonnegative and finite.")


class _Execution:
    def __init__(
        self,
        dag: PhysicalDAG,
        backend: MeasurementBackend,
        durations: Mapping[str, float],
        latency: float,
    ) -> None:
        self.dag = dag
        self.backend = backend
        self.durations = durations
        self.latency = latency
        self.successors: list[list[int]] = [[] for _ in dag.nodes]
        self.remaining = [0] * len(dag.nodes)
        for source, target in dag.edges:
            self.successors[source].append(target)
            self.remaining[target] += 1
        self.queue: list[tuple[float, Literal["complete", "result"], int]] = []
        self.waiting: dict[ResultRef, list[int]] = defaultdict(list)
        self.results: dict[ResultRef, int] = {}
        self.operations: dict[int, OperationEvent] = {}
        self.measurements: list[MeasurementEvent] = []

    def run(self) -> ExecutionTrace:
        for operation_id, remaining in enumerate(self.remaining):
            if remaining == 0:
                self.start(operation_id, 0.0)
        while self.queue:
            time, kind, operation_id = heappop(self.queue)
            if kind == "complete":
                self.complete(operation_id, time)
            else:
                self.report(operation_id, time)
        return ExecutionTrace(
            operations=tuple(
                self.operations[index] for index in range(len(self.dag.nodes))
            ),
            measurements=tuple(self.measurements),
        )

    def start(self, operation_id: int, time: float) -> None:
        node = self.dag.nodes[operation_id]
        condition = node.condition
        if condition is not None and condition.result not in self.results:
            self.waiting[condition.result].append(operation_id)
            return
        enabled = condition is None or self.results[condition.result] == condition.value
        duration = self.durations.get(node.name, 1.0) if enabled else 0.0
        event = OperationEvent(
            operation_id, "executed" if enabled else "skipped", time, time + duration
        )
        self.operations[operation_id] = event
        heappush(self.queue, (event.end, "complete", operation_id))

    def complete(self, operation_id: int, time: float) -> None:
        node = self.dag.nodes[operation_id]
        if node.name == "measure":
            heappush(self.queue, (time + self.latency, "result", operation_id))
        for successor in self.successors[operation_id]:
            self.remaining[successor] -= 1
            if self.remaining[successor] == 0:
                self.start(successor, time)

    def report(self, operation_id: int, time: float) -> None:
        result = ResultRef(operation_id)
        value = self.backend.measure(result)
        if value not in (0, 1):
            raise ValueError(
                f"Measurement {operation_id} returned a non-bit: {value!r}."
            )
        self.results[result] = value
        self.measurements.append(MeasurementEvent(result, value, time))
        for successor in self.waiting.pop(result, ()):
            self.start(successor, time)
