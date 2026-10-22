"""Compile one measurement case from a native DAG to atom actions."""

from dataclasses import dataclass
from typing import Literal, Protocol

from .atom_ir import AtomProgram, AtomStep, Measure, Wait
from .contracts import AtomFragment, GateLayer
from .device import Device
from .execution import FakeBackend, MeasurementBackend
from .physical_ir import PhysicalDAG, ResultRef
from .scheduling.lookahead import next_cz_layer
from .state import AtomLayout, AtomState, validate_layout


class CompilationStrategy(Protocol):
    """Select ready operations and construct their atom-action fragments."""

    @property
    def lookahead_enabled(self) -> bool:
        """Whether lowering uses a preview of the following CZ layer."""
        ...

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Select resolved, time-ready nodes without changing scheduler state."""
        ...

    def lower(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> AtomFragment:
        """Construct a fragment; following/retention are optional hints to ignore."""
        ...


@dataclass(frozen=True, slots=True)
class CompiledOperation:
    """A native node's decision and emitted action interval, in microseconds."""

    operation_id: int
    status: Literal["emitted", "skipped", "metadata"]
    start_us: float
    end_us: float


@dataclass(frozen=True, slots=True)
class MeasurementRecord:
    """One sampled source result and its planned availability in microseconds."""

    result: ResultRef
    value: int
    available_at_us: float


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Case-specific actions, final layout, node decisions, and sampled results.

    Operations follow native DAG ID order; measurement records follow emission
    order. Layers contain only operations passed to the strategy.
    """

    program: AtomProgram
    exit_state: AtomLayout
    operations: tuple[CompiledOperation, ...]
    measurements: tuple[MeasurementRecord, ...]
    layers: tuple[GateLayer, ...]

    @property
    def duration_us(self) -> float:
        """Return completion time including any final pending readout result."""
        return max(
            self.program.duration_us,
            max((item.available_at_us for item in self.measurements), default=0.0),
        )


def compile_physical(
    dag: PhysicalDAG,
    device: Device,
    *,
    strategy: CompilationStrategy,
    entry_state: AtomLayout,
    measurement_backend: MeasurementBackend | None = None,
) -> CompilationResult:
    """Compile a native circuit and one measurement case to Atom IR.

    Args:
        dag: Native operations with wire and result dependencies.
        device: Geometry, controls, and operation timing.
        strategy: Composition of scheduling, placement, transport, and emission.
        entry_state: Valid initial layout with a complete qubit-to-atom binding.
        measurement_backend: Result provider, defaulting to seeded FakeBackend.

    Returns:
        Atom actions with timing, operation decisions, results, and exit state.

    Raises:
        ValueError: If the entry binding is incomplete, a backend returns a
            non-bit, or the selected strategy cannot construct an operation.

    Entry state is checked once. The loop commits constructed fragments and
    does not replay actions or run full transport checks.
    """
    if len(entry_state.qubit_to_atom) != dag.num_qubits:
        raise ValueError("Entry state must bind every physical qubit to an atom.")
    validate_layout(device, entry_state)
    return _Compilation(
        dag,
        device,
        strategy,
        entry_state,
        measurement_backend if measurement_backend is not None else FakeBackend(),
    ).run()


class _Compilation:
    def __init__(
        self,
        dag: PhysicalDAG,
        device: Device,
        strategy: CompilationStrategy,
        layout: AtomLayout,
        backend: MeasurementBackend,
    ) -> None:
        self.dag = dag
        self.device = device
        self.strategy = strategy
        self.layout = layout
        self.retained_atoms: tuple[AtomState, ...] = ()
        self.backend = backend
        self.steps: list[AtomStep] = []
        self.time_us = 0.0
        self.phase = dag.global_phase
        self.operations: dict[int, CompiledOperation] = {}
        self.results: dict[ResultRef, MeasurementRecord] = {}
        self.layers: list[GateLayer] = []
        self.successors: list[list[int]] = [[] for _ in dag.nodes]
        self.remaining = [0] * len(dag.nodes)
        for before, after in dag.edges:
            self.successors[before].append(after)
            self.remaining[after] += 1
        self.ready = {index for index, count in enumerate(self.remaining) if count == 0}

    def run(self) -> CompilationResult:
        while self.ready:
            ready, pending_times = self._time_ready()
            if self._finish_metadata(ready):
                continue
            if not ready:
                if not pending_times:
                    raise ValueError("Ready conditions have no compiled measurement.")
                self._wait_until(min(pending_times))
                continue
            layer = self.strategy.select(self.dag, ready)
            following = self._following(layer)
            fragment = self.strategy.lower(
                self.dag,
                layer,
                self.layout,
                self.device,
                following=following,
                retained_atoms=self.retained_atoms,
            )
            if not fragment.completed_operation_ids:
                raise ValueError(
                    "A selected layer must complete at least one operation."
                )
            self.layers.append(layer)
            self._commit(fragment)
        return CompilationResult(
            AtomProgram(tuple(self.steps), self.phase),
            self.layout,
            tuple(self.operations[index] for index in range(len(self.dag.nodes))),
            tuple(self.results.values()),
            tuple(self.layers),
        )

    def _following(self, layer: GateLayer) -> GateLayer | None:
        if (
            not self.strategy.lookahead_enabled
            or not layer.operation_ids
            or self.dag.nodes[layer.operation_ids[0]].name != "cz"
        ):
            return None
        return next_cz_layer(
            self.dag,
            layer,
            ready=self.ready,
            remaining=self.remaining,
            successors=self.successors,
            results={
                reference: record.value for reference, record in self.results.items()
            },
            scheduler=self.strategy,
        )

    def _time_ready(self) -> tuple[tuple[int, ...], list[float]]:
        ready = []
        pending = []
        for index in sorted(self.ready):
            condition = self.dag.nodes[index].condition
            if condition is not None:
                result = self.results.get(condition.result)
                if result is None:
                    continue
                if result.available_at_us > self.time_us:
                    pending.append(result.available_at_us)
                    continue
            ready.append(index)
        return tuple(ready), pending

    def _finish_metadata(self, ready: tuple[int, ...]) -> bool:
        changed = False
        for index in ready:
            node = self.dag.nodes[index]
            enabled = (
                node.condition is None
                or self.results[node.condition.result].value == node.condition.value
            )
            if enabled and node.name not in ("barrier", "global_phase"):
                continue
            status: Literal["skipped", "metadata"] = (
                "metadata" if enabled else "skipped"
            )
            if enabled and node.name == "global_phase":
                self.phase += node.params[0]
            self.operations[index] = CompiledOperation(
                index, status, self.time_us, self.time_us
            )
            self._finish(index)
            changed = True
        return changed

    def _commit(self, fragment: AtomFragment) -> None:
        intervals: dict[int, tuple[float, float]] = {}
        for step in fragment.steps:
            for action in step.actions:
                end = self.time_us + action.duration_us
                for index in action.source_ids:
                    start = intervals.get(index, (self.time_us, end))[0]
                    previous_end = intervals.get(index, (start, end))[1]
                    intervals[index] = (start, max(previous_end, end))
                if isinstance(action, Measure):
                    assert self.device.readout is not None
                    for _, result in action.results:
                        value = self.backend.measure(result)
                        if value not in (0, 1):
                            raise ValueError(
                                f"Measurement {result.measurement_id} returned "
                                f"a non-bit: {value!r}."
                            )
                        self.results[result] = MeasurementRecord(
                            result, value, end + self.device.readout.latency_us
                        )
            self.steps.append(step)
            self.time_us += step.duration_us
        self.layout = fragment.exit_layout
        self.retained_atoms = fragment.retained_atoms
        for index in fragment.completed_operation_ids:
            start, end = intervals[index]
            self.operations[index] = CompiledOperation(index, "emitted", start, end)
            self._finish(index)

    def _finish(self, index: int) -> None:
        self.ready.remove(index)
        for successor in self.successors[index]:
            self.remaining[successor] -= 1
            if self.remaining[successor] == 0:
                self.ready.add(successor)

    def _wait_until(self, time_us: float) -> None:
        self.steps.append(AtomStep((Wait(duration_us=time_us - self.time_us),)))
        self.time_us = time_us
