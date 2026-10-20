"""Independent end-to-end checks for strict simultaneous transport batches."""

from collections import Counter
from dataclasses import replace
from itertools import combinations
from math import hypot
from pathlib import Path

import pytest

from nadit_physical_compiler import motion
from nadit_physical_compiler.atom_ir import CZPulse, Load, Measure, Move, Store
from nadit_physical_compiler.compiler import CompilationResult, compile_physical
from nadit_physical_compiler.device import Device, Region, load_device
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.execution import FakeBackend
from nadit_physical_compiler.frontend import load_qasm, loads_qasm
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.physical_ir import PhysicalDAG, ResultRef
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.state import (
    AODHolder,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport.sequential import SequentialTransport
from nadit_physical_compiler.transport.strict import StrictTransport

MODULE = Path(__file__).parents[1]
PAIR_IDS = (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right"))


class RecordingBackend:
    """Record each stable source measurement while returning a chosen test case."""

    def __init__(self, bit: int | None) -> None:
        self.calls: list[ResultRef] = []
        self.bit = bit
        self.seeded = FakeBackend(17)

    def measure(self, result: ResultRef) -> int:
        self.calls.append(result)
        return self.seeded.measure(result) if self.bit is None else self.bit


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


def _entry(device: Device, count: int = 4) -> AtomLayout:
    sites = [site for site in device.slm_sites if site.id.startswith("storage_")]
    atoms = tuple(
        AtomState(37 + index * 13, site.position, SLMHolder(site.id))
        for index, site in enumerate(sites[:count])
    )
    return AtomLayout(atoms, qubit_to_atom=tuple(atom.id for atom in atoms))


def _compile(
    dag: PhysicalDAG,
    device: Device,
    entry: AtomLayout,
    *,
    parallel: bool,
    bit: int | None = 1,
) -> tuple[CompilationResult, RecordingBackend]:
    catalog = GatePairCatalog.from_device(device, "entanglement", PAIR_IDS)
    strategy = SearchSmarterStrategy(
        ReadyScheduler(max_cz_gates=catalog.capacity),
        MultiPairPlacer("transport", catalog),
        StrictTransport("transport") if parallel else SequentialTransport("transport"),
        WholeLayerEmitter(catalog.coverage),
    )
    backend = RecordingBackend(bit)
    result = compile_physical(
        dag,
        device,
        strategy=strategy,
        entry_state=entry,
        measurement_backend=backend,
    )
    return result, backend


def _audit(
    dag: PhysicalDAG, result: CompilationResult, device: Device, entry: AtomLayout
) -> None:
    """Check emitted geometry, time, dependencies, and source coverage."""
    layout = entry
    covered: Counter[int] = Counter()
    outcomes = {record.result: record for record in result.measurements}
    aod = device.aod("transport")
    for start, step in result.program.timeline():
        for action in step.actions:
            covered.update(action.source_ids)
            if isinstance(action, Load):
                checked = check_load(
                    device,
                    layout,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
                assert checked.ok, checked.message
                assert action.duration_us == device.timing.load_us
            elif isinstance(action, Move):
                checked = check_move(device, layout, action.grid, dict(action.targets))
                assert checked.ok, checked.message
                maximum = max(
                    hypot(
                        target.x - layout.atom(atom).position.x,
                        target.y - layout.atom(atom).position.y,
                    )
                    for atom, target in action.targets
                )
                assert action.duration_us == pytest.approx(
                    device.timing.move_duration(maximum)
                )
            elif isinstance(action, Store):
                checked = check_store(
                    device, layout, action.grid, dict(action.bindings)
                )
                assert checked.ok, checked.message
                assert action.duration_us == device.timing.store_us
            elif isinstance(action, CZPulse):
                assert device.cz is not None
                assert isinstance(action.coverage, Region)
                illuminated = [
                    atom
                    for atom in layout.atoms
                    if action.coverage.contains(atom.position)
                ]
                actual = {
                    frozenset((first.id, second.id))
                    for first, second in combinations(illuminated, 2)
                    if hypot(
                        first.position.x - second.position.x,
                        first.position.y - second.position.y,
                    )
                    <= device.cz.radius_um
                }
                intended = {
                    frozenset(entry.qubit_to_atom[q] for q in dag.nodes[index].qubits)
                    for index in action.source_ids
                }
                assert actual == intended
                assert {atom.id for atom in illuminated} == set().union(*intended)
                for index in action.source_ids:
                    record = result.operations[index]
                    assert record.start_us == start
                    assert record.end_us == start + device.cz.duration_us
            elif isinstance(action, Measure):
                assert device.readout is not None
                for atom_id, reference in action.results:
                    atom = layout.atom(atom_id)
                    assert isinstance(atom.holder, AODHolder)
                    assert device.zones_at(atom.position, "measure")
                    assert outcomes[reference].available_at_us == pytest.approx(
                        start + action.duration_us + device.readout.latency_us
                    )
            if isinstance(action, Load | Move | Store):
                if aod.max_columns is not None:
                    assert len(action.grid.columns) <= aod.max_columns
                if aod.max_rows is not None:
                    assert len(action.grid.rows) <= aod.max_rows
        layout = apply_step(layout, step, device)
        validate_layout(device, layout)
    assert layout == result.exit_state == replay(result.program, entry, device)
    for record in result.operations:
        assert covered[record.operation_id] == (1 if record.status == "emitted" else 0)
    for before, after in dag.edges:
        assert result.operations[before].end_us <= result.operations[after].start_us
    for node, record in zip(dag.nodes, result.operations, strict=True):
        if node.condition is not None:
            measurement = outcomes[node.condition.result]
            assert record.start_us >= measurement.available_at_us
            assert (record.status == "skipped") == (
                node.condition.value != measurement.value
            )


def _transport_cost(result: CompilationResult) -> tuple[int, float]:
    actions = [
        action
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, Load | Move | Store)
    ]
    return len(actions), sum(action.duration_us for action in actions)


@pytest.mark.parametrize("bit", [0, 1])
def test_strict_feedback_matches_sequential_case_and_reduces_transport(
    device: Device, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)
    strict, strict_backend = _compile(dag, device, entry, parallel=True, bit=bit)
    sequential, sequential_backend = _compile(
        dag, device, entry, parallel=False, bit=bit
    )
    _audit(dag, strict, device, entry)
    _audit(dag, sequential, device, entry)

    assert [(item.operation_id, item.status) for item in strict.operations] == [
        (item.operation_id, item.status) for item in sequential.operations
    ]
    assert [(item.result, item.value) for item in strict.measurements] == [
        (item.result, item.value) for item in sequential.measurements
    ]
    references = [node.result for node in dag.nodes if node.name == "measure"]
    assert strict_backend.calls == sequential_backend.calls == references
    assert strict.program.global_phase == sequential.program.global_phase
    assert strict.exit_state == sequential.exit_state == entry
    assert strict.layers == sequential.layers
    strict_phases, strict_us = _transport_cost(strict)
    sequential_phases, sequential_us = _transport_cost(sequential)
    assert strict_phases < sequential_phases
    assert strict_us < sequential_us
    assert strict.duration_us < sequential.duration_us
    actions = [action for step in strict.program.steps for action in step.actions]
    assert any(
        isinstance(action, Load) and len(action.bindings) > 1 for action in actions
    )
    assert any(
        isinstance(action, Move) and len(action.targets) > 1 for action in actions
    )
    assert entry == _entry(device)


@pytest.mark.parametrize("columns", [1, 2, None])
def test_strict_batches_respect_column_capacity(
    device: Device, columns: int | None
) -> None:
    device = replace(
        device, aods=(replace(device.aod("transport"), max_columns=columns),)
    )
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)

    result, _ = _compile(dag, device, entry, parallel=True)

    _audit(dag, result, device, entry)
    batches = [
        action
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, Load)
    ]
    if columns == 1:
        assert all(len(action.bindings) == 1 for action in batches)
    else:
        assert any(len(action.bindings) > 1 for action in batches)


def test_six_atoms_across_storage_rows_compile_with_gate_capacity_two(
    device: Device,
) -> None:
    dag = loads_qasm(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[6] q; '
        "cz q[0],q[1]; cz q[2],q[3]; cz q[4],q[5];"
    )
    entry = _entry(device, 6)

    result, backend = _compile(dag, device, entry, parallel=True)

    _audit(dag, result, device, entry)
    assert [
        len(action.source_ids)
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    ] == [2, 1]
    assert result.exit_state == entry
    assert backend.calls == []


def test_strict_compilation_is_seeded_repeatable_without_full_transport_checks(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Full transport reference checks must stay outside compilation.")

    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)

    first, first_backend = _compile(dag, device, entry, parallel=True, bit=None)
    second, second_backend = _compile(dag, device, entry, parallel=True, bit=None)

    assert first == second
    assert first.program.to_text() == second.program.to_text()
    assert first_backend.calls == second_backend.calls
    assert len(first_backend.calls) == len(first.measurements) == 1
    assert entry == _entry(device)
