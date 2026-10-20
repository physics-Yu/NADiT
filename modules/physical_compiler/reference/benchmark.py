"""Pinned paper benchmark inputs, settings, and action-level metrics."""

# Adapted from MQT QMAP eval/na/zoned/eval_ids_relaxed_routing.py (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from math import hypot
from pathlib import Path
from typing import Any

PAPER_CASES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("graphstate", (60, 80, 100, 120, 140, 160, 180, 200, 500, 1000, 2000, 5000)),
    ("qft", (500, 1000)),
    ("qpeexact", (500, 1000)),
    ("wstate", (500, 1000)),
    ("qaoa", (50, 100, 150, 200)),
    ("vqe_two_local", (50, 100, 150, 200)),
)
VARIANTS = ("astar_strict", "ids_strict", "ids_relaxed")


def paper_settings(variant: str) -> dict[str, Any]:
    """Return the pinned evaluation script's settings, including A* overrides."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown source benchmark variant: {variant}")
    config: dict[str, Any] = {
        "max_filling_factor": 0.9,
        "use_window": True,
        "window_min_width": 16,
        "window_ratio": 1.0,
        "window_share": 0.8,
        "method": "astar" if variant == "astar_strict" else "ids",
        "routing": "relaxed" if variant == "ids_relaxed" else "strict",
        "deepening_factor": 0.9 if variant == "astar_strict" else 0.01,
        "deepening_value": 8.0 if variant == "astar_strict" else 0.0,
        "lookahead_factor": 0.2 if variant == "astar_strict" else 0.4,
        "reuse_level": 5.0,
        "max_nodes": 10_000_000,
        "trials": 4,
        "queue_capacity": 100,
        "prefer_split": 1.0,
    }
    return config


def paper_architecture() -> dict[str, Any]:
    """Return the exact square architecture used by the pinned evaluation."""
    return {
        "slms": [
            {
                "id": 0,
                "pair_group": -1,
                "rows": 73,
                "columns": 101,
                "pitch": [4, 4],
                "origin": [0, 0],
            },
            {
                "id": 1,
                "pair_group": 0,
                "rows": 10,
                "columns": 34,
                "pitch": [12, 10],
                "origin": [1, 309],
            },
            {
                "id": 2,
                "pair_group": 0,
                "rows": 10,
                "columns": 34,
                "pitch": [12, 10],
                "origin": [3, 309],
            },
        ],
        "timing": {
            "load_us": 15,
            "store_us": 15,
            "move_us_per_um": 0,
            "move_profile": "paper",
        },
        "aod": {"min_spacing": 2, "max_rows": 100, "max_columns": 100},
        "gate_duration_us": 0.36,
        "local_duration_us": 52,
        "arch_range": [[0, 0], [400, 400]],
        "rydberg_range": [[[0, 308], [400, 400]]],
    }


def prepare_input(benchmark: str, qubits: int, path: Path) -> dict[str, Any]:
    """Generate and save one shared native input in an external MQT environment.

    Requires mqt.bench==2.1.0 and the evaluation's Qiskit dependencies. No local
    project dependencies are installed or changed by this function.
    """
    bench = importlib.import_module("mqt.bench")
    qiskit = importlib.import_module("qiskit")
    qasm2 = importlib.import_module("qiskit.qasm2")
    source = bench.get_benchmark(benchmark, bench.BenchmarkLevel.INDEP, qubits)
    flattened = qiskit.QuantumCircuit(source.num_qubits, source.num_clbits)
    flattened.compose(source, inplace=True)
    native = qiskit.transpile(
        flattened,
        basis_gates=["cz", "id", "u2", "u1", "u3"],
        optimization_level=3,
        seed_transpiler=0,
    )
    stripped = qiskit.QuantumCircuit(*native.qregs, *native.cregs)
    operations: list[dict[str, Any]] = []
    for instruction in native.data:
        operation = instruction.operation
        if operation.name in {"measure", "barrier"}:
            continue
        stripped.append(instruction)
        targets = [native.find_bit(bit).index for bit in instruction.qubits]
        parameters = [float(value) for value in operation.params]
        match operation.name:
            case "cz":
                kind = "CZ"
            case "id":
                kind, parameters = "U", [0.0, 0.0, 0.0]
            case "u1":
                # The source importer calls this P and CodeGenerator emits RZ.
                kind = "P"
            case "u2":
                from math import pi

                kind, parameters = "U", [pi / 2, *parameters]
            case "u3":
                kind = "U"
            case _:
                raise ValueError(f"Unexpected preprocessed gate {operation.name}")
        operations.append(
            {
                "type": kind,
                "targets": targets,
                "parameters": parameters,
            }
        )
    qasm = qasm2.dumps(stripped)
    path.with_suffix(".qasm").write_text(qasm, encoding="utf-8")
    case = {
        "name": f"{benchmark}_{qubits}",
        "n_qubits": source.num_qubits,
        "operations": operations,
        **paper_architecture(),
        "preprocessing": {
            "benchmark": benchmark,
            "level": "INDEP",
            "basis": ["cz", "id", "u2", "u1", "u3"],
            "optimization_level": 3,
            "seed_transpiler": 0,
            "qasm_sha256": hashlib.sha256(qasm.encode()).hexdigest(),
            "versions": {
                name: importlib.metadata.version(name)
                for name in ("mqt.bench", "mqt.qmap", "qiskit", "numpy")
            },
        },
    }
    path.write_text(json.dumps(case, separators=(",", ":")) + "\n", encoding="utf-8")
    return {
        "name": case["name"],
        "n_qubits": source.num_qubits,
        "native_operations": len(operations),
        "cz_gates": sum(op["type"] == "CZ" for op in operations),
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def source_metrics(trace: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate raw original events with the paper's transfer/movement model."""
    timing = case.get("timing", {})
    load_us = timing.get("load_us", 15)
    store_us = timing.get("store_us", 15)
    positions = list(trace["initial"])
    counts = {
        "load_actions": 0,
        "store_actions": 0,
        "loaded_atoms": 0,
        "stored_atoms": 0,
        "move_actions": 0,
        "movement_us": 0.0,
        "transfer_us": 0.0,
    }
    for action in trace["actions"]:
        kind, atoms = action["kind"], action["atoms"]
        if kind == "load":
            counts["load_actions"] += 1
            counts["loaded_atoms"] += len(atoms)
            counts["transfer_us"] += load_us
        elif kind == "store":
            counts["store_actions"] += 1
            counts["stored_atoms"] += len(atoms)
            counts["transfer_us"] += store_us
        elif kind == "move":
            distance = max(
                (
                    hypot(
                        target[0] - positions[atom][0], target[1] - positions[atom][1]
                    )
                    for atom, target in zip(atoms, action["targets"], strict=True)
                ),
                default=0,
            )
            duration = (
                200 * (distance / 110) ** (1 / 3)
                if distance <= 110
                else 200 + (distance - 110) / 1.1
            )
            counts["move_actions"] += 1
            counts["movement_us"] += duration
            for atom, target in zip(atoms, action["targets"], strict=True):
                positions[atom] = target
    return {
        **counts,
        "rearrangement_us": counts["movement_us"] + counts["transfer_us"],
        "two_qubit_layers": len(trace["two_layers"]),
        "stage_times_s": trace.get("timings", {}),
    }


def compare_completed_runs(
    manifest: dict[str, Any],
    root: Path,
    names: list[str],
    variants: list[str],
) -> dict[str, Any]:
    """Compare current successful pairs and remove superseded comparisons.

    Raw traces may remain after a failed rerun. Only the current run manifest
    authorizes their use as successful evidence; unrelated comparisons remain.
    """
    from reference.pipeline_candidate import compare_stages

    comparisons = dict(manifest.get("comparisons", {}))
    for name in names:
        for variant in variants:
            key = f"{name}/{variant}"
            comparisons.pop(key, None)
            records = [
                manifest["runs"].get(f"{name}/{engine}/{variant}", {})
                for engine in ("source", "local")
            ]
            if all(record.get("status") == "ok" for record in records):
                source, local = (
                    json.loads(
                        (root / record["trace_path"]).read_text(encoding="utf-8")
                    )["trace"]
                    for record in records
                )
                comparisons[key] = compare_stages(source, local)
    return comparisons
