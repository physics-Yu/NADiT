"""Saved-job examples keep their existing CLI and compile through the job API."""

import json
from pathlib import Path
from typing import Any

import pytest

from examples import compile_multipair, compile_pair
from nadit_physical_compiler.compiler import CompilationResult
from nadit_physical_compiler.job import CompilationJob, compile_job, load_job

MODULE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["pair", "multipair"])
def test_saved_jobs_compile_without_script_composition(name: str) -> None:
    job = load_job(MODULE / "examples" / "jobs" / f"{name}.json")
    result = compile_job(job)
    assert result.program.steps
    assert result.measurements
    assert job.document["case"] == {"seed": 17, "overrides": {}}


@pytest.mark.parametrize("name", ["pair", "multipair"])
@pytest.mark.parametrize("bit", [0, 1])
def test_script_overrides_use_actual_measurement_ids_and_saved_case(
    name: str,
    bit: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module: Any = compile_pair if name == "pair" else compile_multipair
    captured: list[CompilationJob] = []

    def record_job(job: CompilationJob) -> CompilationResult:
        captured.append(job)
        return compile_job(job)

    monkeypatch.setattr(module, "compile_job", record_job)
    monkeypatch.setattr(
        "sys.argv", [f"compile_{name}.py", "--seed", "23", "--bit", str(bit)]
    )
    module.main()
    output = capsys.readouterr().out
    summary, offset = json.JSONDecoder().raw_decode(output)
    job = captured[0]
    references = {
        str(node.result.measurement_id): bit
        for node in job.dag.nodes
        if node.result is not None
    }
    assert job.document["case"] == {"seed": 23, "overrides": references}
    assert all(item["value"] == bit for item in summary["measurements"])
    assert output[offset:].strip() == compile_job(job).program.to_text().strip()


def test_multipair_preserves_all_existing_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[CompilationJob] = []

    def record_job(job: CompilationJob) -> CompilationResult:
        captured.append(job)
        return compile_job(job)

    monkeypatch.setattr(compile_multipair, "compile_job", record_job)
    monkeypatch.setattr(
        "sys.argv",
        [
            "compile_multipair.py",
            "--capacity",
            "1",
            "--placement",
            "ids",
            "--transport",
            "relaxed",
            "--prefer-split",
            "2.5",
            "--reuse",
            "--trials",
            "2",
            "--queue-capacity",
            "20",
            "--reuse-level",
            "3",
            "--no-window",
            "--window-min-width",
            "8",
            "--window-ratio",
            "0.5",
            "--window-share",
            "0.7",
            "--bit",
            "1",
        ],
    )
    compile_multipair.main()
    summary, _ = json.JSONDecoder().raw_decode(capsys.readouterr().out)
    job = captured[0]
    assert job.catalog is not None
    assert job.catalog.capacity == 1
    assert summary["pair_capacity"] == 1
    assert summary["placement"] == "ids"
    assert summary["transport"] == "relaxed"
    assert summary["prefer_split"] == 2.5
    assert summary["reuse"] is True
    assert summary["placement_config"] == {
        key: value
        for key, value in job.document["strategy"]["placement"].items()
        if key not in ("kind", "geometry", "storage_zone_id")
    }
    assert summary["placement_config"]["trials"] == 2
    assert summary["placement_config"]["queue_capacity"] == 20
    assert summary["placement_config"]["reuse_level"] == 3.0
    assert summary["placement_config"]["use_window"] is False
    assert summary["placement_config"]["window_min_width"] == 8
    assert summary["placement_config"]["window_ratio"] == 0.5
    assert summary["placement_config"]["window_share"] == 0.7
