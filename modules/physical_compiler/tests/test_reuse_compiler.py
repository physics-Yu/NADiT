"""Matching reuse through strict transport, feedback, and independent replay."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler import (
    AtomLayout,
    CompilationResult,
    CZPulse,
    Device,
    GateLayer,
    Load,
    Move,
    PhysicalDAG,
    PlacementPlan,
    ResultRef,
    SLMHolder,
    Store,
    apply_step,
    compile_physical,
    load_device,
    load_qasm,
    loads_qasm,
)
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import StrictTransport

MODULE = Path(__file__).parents[1]
PAIRS = (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right"))


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


def _dag(body: str, count: int = 4) -> PhysicalDAG:
    return loads_qasm(
        f'OPENQASM 3.0; include "stdgates.inc"; qubit[{count}] q; bit c; {body}'
    )


def _strategy(
    device: Device, *, reuse: bool = True, capacity: int = 2
) -> SearchSmarterStrategy:
    catalog = GatePairCatalog.from_device(device, "entanglement", PAIRS[:capacity])
    return SearchSmarterStrategy(
        ReadyScheduler(capacity),
        MultiPairPlacer("transport", catalog),
        StrictTransport("transport"),
        WholeLayerEmitter(catalog.coverage),
        reuse_analyzer=MatchingReuseAnalyzer() if reuse else None,
    )


def _compile(
    dag: PhysicalDAG,
    device: Device,
    *,
    reuse: bool = True,
    capacity: int = 2,
    bit: int = 1,
) -> tuple[CompilationResult, AtomLayout, RecordingBackend]:
    entry = _entry(device, dag.num_qubits)
    backend = RecordingBackend(bit)
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(device, reuse=reuse, capacity=capacity),
        entry_state=entry,
        measurement_backend=backend,
    )
    _audit(dag, result, device, entry)
    assert all(
        isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id.startswith("storage_")
        for atom in result.exit_state.atoms
    )
    return result, entry, backend


def _pulses(
    result: CompilationResult, entry: AtomLayout, device: Device
) -> list[tuple[int, AtomLayout]]:
    layout = entry
    pulses = []
    for index, step in enumerate(result.program.steps):
        if any(isinstance(action, CZPulse) for action in step.actions):
            pulses.append((index, layout))
        layout = apply_step(layout, step, device)
    return pulses


def _load_count(result: CompilationResult) -> int:
    return sum(
        len(action.bindings)
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, Load)
    )


def test_reuse_is_an_explicit_strategy_option(device: Device) -> None:
    assert not _strategy(device, reuse=False).lookahead_enabled
    assert _strategy(device).lookahead_enabled


def test_partner_changes_keep_two_atoms_stationary_and_reduce_atom_transfers(
    device: Device,
) -> None:
    dag = _dag("cz q[0],q[1]; cz q[2],q[3]; cz q[0],q[2]; cz q[1],q[3];")
    reused, entry, backend = _compile(dag, device)
    baseline, _, _ = _compile(dag, device, reuse=False)
    (first_step, first), (second_step, second) = _pulses(reused, entry, device)
    stationary = {atom.id for atom in first.atoms if atom == second.atom(atom.id)}
    assert len(stationary) == 2
    transfers = [
        action
        for step in reused.program.steps[first_step + 1 : second_step]
        for action in step.actions
        if isinstance(action, Load)
    ]
    assert transfers
    assert all(
        stationary.isdisjoint(atom for atom, _, _ in action.bindings)
        for action in transfers
    )
    assert _load_count(reused) < _load_count(baseline)
    assert reused.layers == baseline.layers
    assert backend.calls == []


@pytest.mark.parametrize(
    "middle",
    ["", "barrier q; rx(0.3) q[0]; rz(0.2) q[3]; barrier q;"],
)
def test_identical_pairs_retain_all_atoms_across_local_operations(
    device: Device, middle: str
) -> None:
    dag = _dag(f"cz q[0],q[1]; cz q[2],q[3]; {middle} cz q[1],q[0]; cz q[3],q[2];")
    result, entry, _ = _compile(dag, device)
    (first_step, first), (second_step, second) = _pulses(result, entry, device)
    assert first == second
    assert not any(
        isinstance(action, Load | Move | Store)
        for step in result.program.steps[first_step + 1 : second_step]
        for action in step.actions
    )


@pytest.mark.parametrize("bit", [0, 1])
def test_existing_feedback_cases_preserve_decisions_and_replay(
    device: Device, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    reused, _, backend = _compile(dag, device, bit=bit)
    baseline, _, baseline_backend = _compile(dag, device, reuse=False, bit=bit)
    assert [(item.operation_id, item.status) for item in reused.operations] == [
        (item.operation_id, item.status) for item in baseline.operations
    ]
    assert [(item.result, item.value) for item in reused.measurements] == [
        (item.result, item.value) for item in baseline.measurements
    ]
    assert reused.program.global_phase == baseline.program.global_phase
    assert reused.layers == baseline.layers
    assert (
        backend.calls
        == baseline_backend.calls
        == [node.result for node in dag.nodes if node.result is not None]
    )
    assert _load_count(reused) < _load_count(baseline)


@pytest.mark.parametrize("bit", [0, 1])
def test_measurement_blocks_preview_and_is_sampled_only_when_emitted(
    device: Device, bit: int
) -> None:
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "c = measure q[0]; "
        "if(c) { cz q[0],q[2]; cz q[1],q[3]; } "
        "else { rx(0.4) q[1]; }"
    )
    entry = _entry(device)
    backend = RecordingBackend(bit)
    strategy = _strategy(device)
    calls: list[tuple[str, tuple[int, ...], tuple[ResultRef, ...]]] = []

    class ObservedPlacer:
        def place(
            self,
            dag: PhysicalDAG,
            layer: GateLayer,
            layout: AtomLayout,
            device: Device,
            *,
            reuse_qubits: tuple[int, ...] = (),
            following: GateLayer | None = None,
        ) -> PlacementPlan:
            calls.append(
                (
                    dag.nodes[layer.operation_ids[0]].name,
                    reuse_qubits,
                    tuple(backend.calls),
                )
            )
            return strategy.placer.place(
                dag,
                layer,
                layout,
                device,
                reuse_qubits=reuse_qubits,
                following=following,
            )

    result = compile_physical(
        dag,
        device,
        strategy=replace(strategy, placer=ObservedPlacer()),
        entry_state=entry,
        measurement_backend=backend,
    )
    _audit(dag, result, device, entry)
    assert calls[0] == ("cz", (), ())
    assert next(item for item in calls if item[0] == "measure") == (
        "measure",
        (),
        (),
    )
    assert len(backend.calls) == 1
    assert all(item[1] == () for item in calls)


@pytest.mark.parametrize("bit", [0, 1])
def test_resolved_conditions_allow_reuse_through_the_selected_branch(
    device: Device, bit: int
) -> None:
    dag = _dag(
        "c = measure q[0]; barrier q; "
        "if(c) { cz q[0],q[1]; cz q[2],q[3]; "
        "rz(0.2) q[0]; cz q[0],q[2]; cz q[1],q[3]; } "
        "else { cz q[0],q[2]; cz q[1],q[3]; "
        "rx(0.7) q[1]; cz q[0],q[2]; cz q[1],q[3]; }"
    )
    result, entry, backend = _compile(dag, device, bit=bit)
    (_, first), (_, second) = _pulses(result, entry, device)
    assert sum(atom == second.atom(atom.id) for atom in first.atoms) == (
        2 if bit else 4
    )
    assert len(backend.calls) == 1


@pytest.mark.parametrize("capacity", [1, 2])
def test_capacity_splits_and_three_partner_layers(
    device: Device, capacity: int
) -> None:
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; cz q[4],q[5]; barrier q; "
        "cz q[0],q[2]; cz q[1],q[4]; cz q[3],q[5]; barrier q; "
        "cz q[0],q[5]; cz q[1],q[3]; cz q[2],q[4];",
        6,
    )
    result, _, backend = _compile(dag, device, capacity=capacity)
    assert all(
        len(action.source_ids) <= capacity
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    )
    assert backend.calls == []


def test_reuse_output_is_deterministic(device: Device) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    first, _, _ = _compile(dag, device)
    second, _, _ = _compile(dag, device)
    assert first == second
    assert first.program.to_text() == second.program.to_text()
