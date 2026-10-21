"""Original complete C++ traces compared with actual production stage handoffs."""

import copy
import json
from itertools import combinations
from math import hypot
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.atom_ir import CZPulse, Load, Move, Store
from nadit_physical_compiler.compiler import CompilationResult, compile_physical
from nadit_physical_compiler.coverage import RegionSet
from nadit_physical_compiler.device import Device, Region
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.state import validate_layout
from reference import pipeline_candidate
from reference.pipeline_candidate import (
    compare_stages,
    load_cases,
    native_dag,
    run_case,
)

HERE = Path(__file__).parents[1] / "reference"
CASES = load_cases()


@pytest.fixture(scope="module")
def source() -> dict[str, Any]:
    return dict(json.loads((HERE / "pipeline_expected.json").read_text())["cases"])


@pytest.fixture(scope="module")
def observations() -> dict[str, dict[str, Any]]:
    return {case["name"]: run_case(case) for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_complete_pipeline_matches_source_or_preserves_its_failure(
    case: dict[str, Any],
    source: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> None:
    expected = source[case["name"]]
    actual = observations[case["name"]]
    comparison = compare_stages(expected, actual)
    if case["name"] == "global_ry_and_barrier":
        # ADR 0023 restores the rotation omitted by the pinned source output.
        # Keep the original oracle and check the exact resulting divergence.
        assert expected["status"] == "ok"
        assert comparison == {
            "status": "different",
            "first_stage": "placement",
            "path": "placements[3][0][2]",
            "expected": 1,
            "actual": 0,
        }
        assert len(expected["result"]["actions"]) == 28
        assert len(actual["actions"]) == 33
    elif expected["status"] == "ok":
        assert comparison == {"status": "matched", "first_stage": None}
    else:
        assert expected["status"] in ("timeout", "source_failure")
        assert comparison["status"] == "source_unavailable"
        assert comparison["source_status"] == expected["status"]
        assert comparison["first_stage"] == "placement"
        assert actual["actions"]
    assert actual["metrics"]["compilation_s"] >= 0


def test_complete_edge_correction_first_differs_at_reuse(
    source: dict[str, Any],
) -> None:
    case = next(
        case for case in CASES if case["name"] == "source_endpoint_priority_reuse"
    )
    actual = run_case({**case, "reuse_policy": "complete_edges"})
    comparison = compare_stages(source[case["name"]], actual)
    assert comparison["first_stage"] == "reuse"
    assert actual["reuse"] == [[1, 2]]


@pytest.mark.parametrize(
    ("field", "stage"),
    [
        ("single_layers", "scheduling"),
        ("two_layers", "scheduling"),
        ("reuse", "reuse"),
        ("placements", "placement"),
        ("routings", "routing"),
        ("actions", "actions"),
    ],
)
def test_comparator_stops_at_first_changed_stage(
    field: str, stage: str, source: dict[str, Any]
) -> None:
    original = source["parallel_pairs_partner_change"]["result"]
    altered = copy.deepcopy(original)
    altered[field].append(
        copy.deepcopy(altered[field][-1]) if field == "actions" else []
    )
    # A later action difference must not hide an earlier stage difference.
    if field != "actions":
        altered["actions"].append(copy.deepcopy(altered["actions"][-1]))
    comparison = compare_stages(original, altered)
    assert comparison["first_stage"] == stage


def test_comparator_keeps_batch_order_but_ignores_parallel_member_order(
    source: dict[str, Any],
) -> None:
    original = source["parallel_pairs_partner_change"]["result"]
    reordered = copy.deepcopy(original)
    for transition in reordered["routings"]:
        for batch in transition:
            batch.reverse()
    for action in reordered["actions"]:
        action["atoms"].reverse()
        if action["targets"]:
            action["targets"].reverse()
    assert compare_stages(original, reordered)["status"] == "matched"
    reordered["actions"][0], reordered["actions"][1] = (
        reordered["actions"][1],
        reordered["actions"][0],
    )
    assert compare_stages(original, reordered)["first_stage"] == "actions"


def test_native_adapter_keeps_original_node_ids_and_wire_dependencies() -> None:
    dag = native_dag(
        {
            "n_qubits": 3,
            "operations": [
                {"type": "CZ", "targets": [1, 0]},
                {"type": "RZ", "targets": [2], "parameters": [0.2]},
                {"type": "RY", "targets": [0, 1, 2], "parameters": [0.4]},
                {"type": "U", "targets": [1], "parameters": [0.1, 0.2, 0.3]},
            ],
        }
    )
    assert [node.name for node in dag.nodes] == ["cz", "rz", "global_ry", "u"]
    assert dag.nodes[0].qubits == (0, 1)
    assert dag.edges == ((0, 2), (1, 2), (2, 3))


def test_multizone_source_batch_passes_independent_legality_and_final_replay(
    monkeypatch: pytest.MonkeyPatch, source: dict[str, Any]
) -> None:
    audited: list[CompilationResult] = []

    def checked_compile(
        dag: PhysicalDAG, device: Device, **kwargs: Any
    ) -> CompilationResult:
        result = compile_physical(dag, device, **kwargs)
        layout = kwargs["entry_state"]
        for step in result.program.steps:
            for action in step.actions:
                if isinstance(action, Load):
                    checked = check_load(
                        device,
                        layout,
                        action.grid,
                        {atom: (row, column) for atom, row, column in action.bindings},
                    )
                    assert checked.ok, checked.message
                elif isinstance(action, Move):
                    checked = check_move(
                        device, layout, action.grid, dict(action.targets)
                    )
                    assert checked.ok, checked.message
                elif isinstance(action, Store):
                    checked = check_store(
                        device, layout, action.grid, dict(action.bindings)
                    )
                    assert checked.ok, checked.message
                elif isinstance(action, CZPulse):
                    assert isinstance(action.coverage, Region | RegionSet)
                    assert device.cz is not None
                    illuminated = [
                        atom
                        for atom in layout.atoms
                        if action.coverage.contains(atom.position)
                    ]
                    actual = {
                        frozenset((a.id, b.id))
                        for a, b in combinations(illuminated, 2)
                        if hypot(
                            a.position.x - b.position.x, a.position.y - b.position.y
                        )
                        <= device.cz.radius_um
                    }
                    assert actual == {
                        frozenset(
                            layout.qubit_to_atom[q] for q in dag.nodes[index].qubits
                        )
                        for index in action.source_ids
                    }
            layout = apply_step(layout, step, device)
            validate_layout(device, layout)
        assert layout == result.exit_state
        assert layout == replay(result.program, kwargs["entry_state"], device)
        audited.append(result)
        return result

    monkeypatch.setattr(pipeline_candidate, "compile_physical", checked_compile)
    case = next(case for case in CASES if case["name"] == "two_gate_zones")
    result = run_case(case)
    assert len(audited) == 1
    assert result["routings"][0] == [list(range(8))]
    assert compare_stages(source[case["name"]], result)["status"] == "matched"


def test_source_phase_operation_maps_to_its_local_rz_primitive() -> None:
    dag = native_dag(
        {
            "n_qubits": 2,
            "operations": [{"type": "P", "targets": [1], "parameters": [0.25]}],
        }
    )
    assert dag.nodes == (PhysicalOperation(0, "rz", (1,), (), (0.25,)),)


def test_explicit_rydberg_extent_survives_device_actions_and_normalized_trace(
    monkeypatch: pytest.MonkeyPatch, source: dict[str, Any]
) -> None:
    expected_region = Region(0, 70, 60, 100)
    coverages: list[Region | RegionSet | tuple[int, ...]] = []

    def observe_compile(
        dag: PhysicalDAG, device: Device, **kwargs: Any
    ) -> CompilationResult:
        assert next(
            zone.bounds for zone in device.zones if "cz" in zone.operations
        ) == (expected_region)
        result = compile_physical(dag, device, **kwargs)
        coverages.extend(
            action.coverage
            for step in result.program.steps
            for action in step.actions
            if isinstance(action, CZPulse)
        )
        return result

    monkeypatch.setattr(pipeline_candidate, "compile_physical", observe_compile)
    case = next(case for case in CASES if case["name"] == "explicit_rydberg_extent")
    result = run_case(case)
    assert coverages == [expected_region, expected_region]
    assert result["zones"][0]["extent"] == [0, 70, 60, 100]
    assert compare_stages(source[case["name"]], result)["status"] == "matched"


def test_one_illuminated_region_can_cover_two_entanglement_arrays() -> None:
    case = next(case for case in CASES if case["name"] == "two_gate_zones")
    actual = run_case({**case, "rydberg_range": [[[0, 70], [120, 100]]]})
    pulses = [action for action in actual["actions"] if action["kind"] == "global_cz"]
    assert len(actual["two_layers"][0]) == 4
    assert actual["zones"][0] == {"name": "zone_cz0", "extent": [0, 70, 120, 100]}
    assert len(actual["zones"]) == 2  # One CZ region and the source global-RY region.
    assert pulses and all(action["zones"] == [0] for action in pulses)
