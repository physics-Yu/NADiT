"""Paper configuration and trace metrics independent of external benchmarks."""

import json
from pathlib import Path
from typing import Any

import pytest

from reference.benchmark import (
    PAPER_CASES,
    VARIANTS,
    compare_completed_runs,
    paper_architecture,
    paper_settings,
    source_metrics,
)


def test_original_paper_matrix_and_settings() -> None:
    assert sum(len(sizes) for _, sizes in PAPER_CASES) == 26
    assert dict(PAPER_CASES)["graphstate"][-1] == 5000
    assert len(VARIANTS) == 3
    astar = paper_settings("astar_strict")
    assert (
        astar["method"],
        astar["routing"],
        astar["deepening_factor"],
        astar["deepening_value"],
        astar["lookahead_factor"],
    ) == ("astar", "strict", 0.9, 8.0, 0.2)
    assert paper_settings("ids_strict")["deepening_factor"] == 0.01
    assert paper_settings("ids_relaxed")["routing"] == "relaxed"
    with pytest.raises(ValueError, match="Unknown"):
        paper_settings("invented")


def test_architecture_preserves_original_capacity_and_timing() -> None:
    arch = paper_architecture()
    assert arch["slms"][0]["rows"] * arch["slms"][0]["columns"] == 7373
    assert arch["slms"][1]["rows"] * arch["slms"][1]["columns"] == 340
    assert arch["aod"] == {"min_spacing": 2, "max_rows": 100, "max_columns": 100}
    assert arch["timing"]["load_us"] == arch["timing"]["store_us"] == 15
    assert arch["timing"]["move_profile"] == "paper"


def test_metrics_use_longest_displacement_and_update_positions() -> None:
    trace: dict[str, Any] = {
        "initial": [[0, 0], [5, 0]],
        "two_layers": [[[0, 1]]],
        "actions": [
            {"kind": "load", "atoms": [0, 1], "targets": []},
            {"kind": "move", "atoms": [0, 1], "targets": [[0, 110], [5, 55]]},
            {"kind": "move", "atoms": [0], "targets": [[0, 330]]},
            {"kind": "store", "atoms": [0, 1], "targets": []},
        ],
    }
    metrics = source_metrics(trace, paper_architecture())
    assert metrics["loaded_atoms"] == metrics["stored_atoms"] == 2
    assert metrics["load_actions"] == metrics["store_actions"] == 1
    assert metrics["move_actions"] == 2
    assert metrics["movement_us"] == 500
    assert metrics["transfer_us"] == 30
    assert metrics["rearrangement_us"] == 530


@pytest.mark.parametrize("status", ["timeout", "error"])
def test_failed_rerun_does_not_reuse_old_success_trace(
    tmp_path: Path, status: str
) -> None:
    for engine in ("source", "local"):
        (tmp_path / f"{engine}.json").write_text("stale trace", encoding="utf-8")
    manifest: dict[str, Any] = {
        "runs": {
            "case/source/ids_strict": {
                "status": "ok",
                "trace_path": "source.json",
            },
            "case/local/ids_strict": {
                "status": status,
                "trace_path": "local.json",
            },
        },
        "comparisons": {
            "case/ids_strict": {"status": "matched", "first_stage": None},
            "other/ids_strict": {"status": "matched", "first_stage": None},
        },
    }
    comparisons = compare_completed_runs(manifest, tmp_path, ["case"], ["ids_strict"])
    assert comparisons == {
        "other/ids_strict": {"status": "matched", "first_stage": None}
    }


def test_current_successful_pair_is_compared(tmp_path: Path) -> None:
    trace: dict[str, Any] = {
        key: []
        for key in (
            "single_layers",
            "two_layers",
            "reuse",
            "placements",
            "routings",
            "zones",
            "initial",
            "actions",
        )
    }
    for engine in ("source", "local"):
        (tmp_path / f"{engine}.json").write_text(
            json.dumps({"trace": trace}), encoding="utf-8"
        )
    manifest = {
        "runs": {
            f"case/{engine}/ids_strict": {
                "status": "ok",
                "trace_path": f"{engine}.json",
            }
            for engine in ("source", "local")
        }
    }
    assert compare_completed_runs(manifest, tmp_path, ["case"], ["ids_strict"]) == {
        "case/ids_strict": {"status": "matched", "first_stage": None}
    }
