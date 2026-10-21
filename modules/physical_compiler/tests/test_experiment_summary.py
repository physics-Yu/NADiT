"""Hand-checked medians, paired ratios, coverage-aware fronts, and outliers."""

import json
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.experiments.journal import append, write_json
from nadit_physical_compiler.experiments.summary import pareto, statistics, summarize
from nadit_physical_compiler.provenance import canonical_hash


def row(identifier: str, motion: str, circuit: str, duration: float) -> dict[str, Any]:
    config = {"model": "additive", "params": {}}
    return {
        "run_id": identifier,
        "architecture_hash": motion,
        "strategy_hash": "ids",
        "architecture": {"aod": {"motion": motion}},
        "strategy": "ids",
        "circuit": circuit,
        "case": {"seed": 0},
        "status": "ok",
        "reason": None,
        "metrics": {
            "duration_us": duration,
            "atom_load_count": 5,
            "atom_store_count": 5,
        },
        "hardware": {"slm_sites": 20},
        "compile": {"wall_s": 1, "budget_hit": False},
        "cost": [
            {
                "id": canonical_hash(config),
                "status": "ok",
                "metrics": {"log_fidelity": -duration / 100},
            }
        ],
        "provenance": {"result_path": None, "job_path": f"jobs/{identifier}.json"},
    }


def test_statistics_and_pareto_are_hand_checked() -> None:
    assert statistics([10, 20, 30, 40]) == {"count": 4, "median": 25, "iqr": 15}
    assert statistics([])["median"] is None
    assert statistics([2])["iqr"] == 0
    assert pareto({"a": (1, 3), "b": (2, 2), "c": (3, 3), "d": (1, 3)}) == [
        "a",
        "b",
        "d",
    ]


def test_summary_ratios_fronts_failures_and_absent_result_commands(
    tmp_path: Path,
) -> None:
    write_json(
        tmp_path / "experiment.json",
        {
            "cost": [{"model": "additive", "params": {}}],
            "baseline": {"architecture": {"aod.motion": "flexible"}, "strategy": "ids"},
        },
    )
    rows = [
        row("f1", "flexible", "c1", 10),
        row("f2", "flexible", "c2", 30),
        row("r1", "rigid", "c1", 20),
        row("r2", "rigid", "c2", 60),
    ]
    failed = row("bad", "rigid", "c3", 1)
    failed.update(status="validation_failed", reason="fixture")
    for item in [*rows, failed]:
        append(tmp_path / "runs.jsonl", item)
    summary = summarize(tmp_path)
    assert summary["statuses"] == {"ok": 4, "validation_failed": 1}
    rigid = next(
        group for group in summary["groups"] if group["architecture_hash"] == "rigid"
    )
    assert rigid["statistics"]["duration_us"]["median"] == 40
    assert rigid["baseline_ratios"]["duration_us"]["geometric_mean"] == pytest.approx(2)
    assert all(
        front == ["flexible:ids"] for front in summary["pareto_by_coverage"].values()
    )
    assert {item["run_id"] for item in summary["outliers"]} == {"r1", "r2", "bad"}
    assert all(
        item["viewer_command"] is None and "nadit compile" in item["reproduce_command"]
        for item in summary["outliers"]
    )
    assert (tmp_path / "summary.csv").exists()
    assert (
        json.loads((tmp_path / "summary.json").read_text())["groups"]
        == summary["groups"]
    )


def test_missing_and_ambiguous_baselines_are_explicit(tmp_path: Path) -> None:
    write_json(
        tmp_path / "experiment.json",
        {
            "cost": [],
            "baseline": {"architecture": {"aod.motion": "flexible"}, "strategy": "ids"},
        },
    )
    append(tmp_path / "runs.jsonl", row("r", "rigid", "c", 20))
    assert summarize(tmp_path)["comparisons"][0]["issues"] == ["missing_baseline"]
    for name in ("f1", "f2"):
        append(tmp_path / "runs.jsonl", row(name, "flexible", "c", 10))
    summary = summarize(tmp_path)
    assert "ambiguous_baseline" in summary["comparisons"][0]["issues"]
