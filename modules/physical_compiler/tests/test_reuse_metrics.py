"""CLI metrics distinguish atom transfer counts from batched transport time."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE = Path(__file__).parents[1]


def _run_example(bit: int, *, reuse: bool) -> tuple[dict[str, Any], str]:
    command = [
        sys.executable,
        str(MODULE / "examples/compile_multipair.py"),
        "--transport",
        "strict",
        "--bit",
        str(bit),
    ]
    if reuse:
        command.append("--reuse")
    completed = subprocess.run(
        command, cwd=MODULE, check=True, capture_output=True, text=True
    )
    metrics, end = json.JSONDecoder().raw_decode(completed.stdout)
    return metrics, completed.stdout[end:].strip()


def _audit_metrics(metrics: dict[str, Any], atom_ir: str) -> tuple[int, ...]:
    lines = atom_ir.splitlines()
    loads = [line for line in lines if line.startswith("LOAD(")]
    stores = [line for line in lines if line.startswith("STORE(")]
    moves = [line for line in lines if line.startswith("MOVE(")]
    pulses = [line for line in lines if line.startswith("CZ_PULSE(")]
    load_sizes = [line.count("->") for line in loads]
    store_sizes = [line.count("->") for line in stores]
    assert metrics["atom_load_count"] == sum(load_sizes)
    assert metrics["atom_store_count"] == sum(store_sizes)
    assert metrics["load_batch_sizes"] == load_sizes
    assert metrics["action_counts"]["Load"] == len(loads)
    assert metrics["action_counts"]["Store"] == len(stores)
    assert metrics["action_counts"]["Move"] == len(moves)
    durations = [
        float(line.partition("duration_us=")[2].partition(",")[0])
        for line in (*loads, *moves, *stores)
    ]
    assert metrics["transport_duration_us"] == pytest.approx(sum(durations))
    assert metrics["transport_duration_us"] > 0
    gate_ids = [
        tuple(int(value) for value in line.partition("# source_ids=")[2].split(","))
        for line in pulses
    ]
    assert metrics["cz_gates_per_pulse"] == [len(ids) for ids in gate_ids]
    assert metrics["replay_matches_exit_state"]
    return tuple(index for ids in gate_ids for index in ids)


@pytest.mark.parametrize("bit", [0, 1])
def test_cli_reuse_metrics_preserve_case_and_count_loads_and_stores(bit: int) -> None:
    baseline, baseline_ir = _run_example(bit, reuse=False)
    reused, reused_ir = _run_example(bit, reuse=True)

    baseline_gates = _audit_metrics(baseline, baseline_ir)
    reused_gates = _audit_metrics(reused, reused_ir)

    assert baseline["reuse"] is False
    assert reused["reuse"] is True
    assert baseline["native_nodes"] == reused["native_nodes"]
    assert baseline["skipped_native_ids"] == reused["skipped_native_ids"]
    assert baseline["cz_gates_per_pulse"] == reused["cz_gates_per_pulse"]
    assert baseline_gates == reused_gates
    assert [
        (item["source_id"], item["value"]) for item in baseline["measurements"]
    ] == [(item["source_id"], item["value"]) for item in reused["measurements"]]
    assert [item["value"] for item in reused["measurements"]] == [bit]
    assert reused["atom_load_count"] == reused["atom_store_count"]
    assert baseline["atom_load_count"] == baseline["atom_store_count"]
    assert reused["atom_load_count"] < baseline["atom_load_count"]
    assert reused["atom_store_count"] < baseline["atom_store_count"]
