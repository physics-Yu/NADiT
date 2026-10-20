"""IDS placement integrates with case compilation and strict atom transport."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler import motion
from nadit_physical_compiler.atom_ir import CZPulse, Load, Move, Store
from nadit_physical_compiler.compiler import CompilationResult, compile_physical
from nadit_physical_compiler.contracts import GateLayer, PlacementPlan, Placer
from nadit_physical_compiler.device import Device, load_device
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.frontend import load_qasm, loads_qasm
from nadit_physical_compiler.physical_ir import PhysicalDAG, ResultRef
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    MultiPairPlacer,
)
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.state import AtomLayout, SLMHolder
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import StrictTransport

MODULE = Path(__file__).parents[1]
PAIRS = (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right"))
type Mode = Literal["ids", "fixed", "no_reuse"]


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


def _dag(body: str, count: int = 4) -> PhysicalDAG:
    return loads_qasm(
        f'OPENQASM 3.0; include "stdgates.inc"; qubit[{count}] q; bit c; {body}'
    )


def _strategy(
    device: Device, mode: Mode = "ids", capacity: int = 2
) -> SearchSmarterStrategy:
    catalog = GatePairCatalog.from_device(device, "entanglement", PAIRS[:capacity])
    placer: Placer = (
        HeuristicPlacer("transport", catalog)
        if mode == "ids"
        else MultiPairPlacer("transport", catalog)
    )
    return SearchSmarterStrategy(
        ReadyScheduler(capacity),
        placer,
        StrictTransport("transport"),
        WholeLayerEmitter(catalog.coverage),
        reuse_analyzer=MatchingReuseAnalyzer() if mode != "no_reuse" else None,
    )


def _compile(
    dag: PhysicalDAG,
    device: Device,
    *,
    mode: Mode = "ids",
    bit: int | None = 1,
    capacity: int = 2,
) -> tuple[CompilationResult, RecordingBackend]:
    entry = _entry(device, dag.num_qubits)
    backend = RecordingBackend(bit)
    result = compile_physical(
        dag,
        device,
        strategy=_strategy(device, mode, capacity),
        entry_state=entry,
        measurement_backend=backend,
    )
    _audit(dag, result, device, entry)
    assert all(
        isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id.startswith("storage_")
        for atom in result.exit_state.atoms
    )
    return result, backend


@pytest.mark.parametrize("bit", [0, 1])
def test_ids_feedback_cases_match_fixed_placement_semantics(
    device: Device, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    ids, backend = _compile(dag, device, bit=bit)

    for mode in ("fixed", "no_reuse"):
        baseline, baseline_backend = _compile(dag, device, mode=mode, bit=bit)
        assert [(item.operation_id, item.status) for item in ids.operations] == [
            (item.operation_id, item.status) for item in baseline.operations
        ]
        assert [(item.result, item.value) for item in ids.measurements] == [
            (item.result, item.value) for item in baseline.measurements
        ]
        assert ids.program.global_phase == baseline.program.global_phase
        assert ids.layers == baseline.layers
        assert backend.calls == baseline_backend.calls
    assert backend.calls == [
        node.result for node in dag.nodes if node.result is not None
    ]


@pytest.mark.parametrize(
    "capacity,rows,columns", [(1, 1, 1), (2, 1, 2), (2, 2, 2), (2, None, None)]
)
def test_ids_partner_changes_respect_pair_and_aod_capacities(
    device: Device, capacity: int, rows: int | None, columns: int | None
) -> None:
    device = replace(
        device,
        aods=(replace(device.aod("transport"), max_rows=rows, max_columns=columns),),
    )
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; cz q[4],q[5]; barrier q; "
        "cz q[0],q[2]; cz q[1],q[4]; cz q[3],q[5]; barrier q; "
        "cz q[0],q[5]; cz q[1],q[3]; cz q[2],q[4];",
        6,
    )

    result, backend = _compile(dag, device, capacity=capacity)

    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, CZPulse):
                assert len(action.source_ids) <= capacity
            if isinstance(action, Load | Move | Store):
                if rows is not None:
                    assert len(action.grid.rows) <= rows
                if columns is not None:
                    assert len(action.grid.columns) <= columns
    assert backend.calls == []


def test_ids_identical_pairs_and_local_layers_are_deterministic(device: Device) -> None:
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "rx(0.3) q[0]; rz(-0.2) q[3]; barrier q; "
        "cz q[1],q[0]; cz q[3],q[2];"
    )

    first, _ = _compile(dag, device, bit=None)
    second, _ = _compile(dag, device, bit=None)

    assert first == second
    assert first.program.to_text() == second.program.to_text()


@pytest.mark.parametrize("bit", [0, 1])
def test_ids_uses_compiled_results_before_latency_but_waits_for_conditional_actions(
    device: Device, bit: int
) -> None:
    assert device.readout is not None
    device = replace(device, readout=replace(device.readout, latency_us=100_000))
    dag = _dag("c = measure q[0]; cz q[1],q[2]; if(c) { rz(0.2) q[1]; } cz q[1],q[2];")
    entry = _entry(device)
    strategy = _strategy(device)
    backend = RecordingBackend(bit)
    observed: list[
        tuple[GateLayer, GateLayer | None, tuple[int, ...], tuple[ResultRef, ...]]
    ] = []

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
            if dag.nodes[layer.operation_ids[0]].name == "cz":
                observed.append((layer, following, reuse_qubits, tuple(backend.calls)))
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
    current, following, retained, calls = observed[0]
    measurement = result.measurements[0]
    assert following == observed[1][0]
    assert retained == (1, 2)
    assert calls == (measurement.result,)
    assert (
        result.operations[current.operation_ids[0]].end_us < measurement.available_at_us
    )
    assert backend.calls == [measurement.result]
    for node in dag.nodes:
        if node.condition is not None:
            assert result.operations[node.id].start_us >= measurement.available_at_us


@pytest.mark.parametrize("bit", [0, 1])
def test_ids_cannot_sample_a_future_measurement_for_lookahead(
    device: Device, bit: int
) -> None:
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; c = measure q[0]; "
        "if(c) { cz q[0],q[2]; cz q[1],q[3]; } else { rx(0.4) q[1]; }"
    )
    entry = _entry(device)
    strategy = _strategy(device)
    backend = RecordingBackend(bit)
    observations: list[tuple[str, GateLayer | None, tuple[ResultRef, ...]]] = []

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
            observations.append(
                (
                    dag.nodes[layer.operation_ids[0]].name,
                    following,
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
    assert observations[0] == ("cz", None, ())
    assert next(item for item in observations if item[0] == "measure") == (
        "measure",
        None,
        (),
    )
    assert backend.calls == [result.measurements[0].result]


def test_ids_construction_does_not_call_full_transport_checks(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Full transport checks must remain test-only.")

    dag = _dag("cz q[0],q[1]; cz q[2],q[3]; cz q[0],q[2]; cz q[1],q[3];")
    entry = _entry(device)
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)

    result = compile_physical(
        dag, device, strategy=_strategy(device), entry_state=entry
    )

    _audit(dag, result, device, entry)
