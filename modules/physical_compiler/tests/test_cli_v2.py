"""The command line validates portable result semantics without original inputs."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from test_cli import _run, _write
from test_result_v2 import _copied_job, _document

from nadit_physical_compiler.job import load_job, loads_job


def _feedback_document(tmp_path: Path, bit: int = 1) -> dict[str, Any]:
    path = _copied_job(tmp_path)
    initial = load_job(path)
    spec = deepcopy(initial.document)
    spec["case"]["overrides"] = {
        str(node.result.measurement_id): bit
        for node in initial.dag.nodes
        if node.result is not None
    }
    return _document(loads_job(json.dumps(spec), base_dir=tmp_path))


@pytest.mark.parametrize("bit", [0, 1])
def test_v2_validate_uses_snapshots_after_input_files_are_removed(
    tmp_path: Path, bit: int
) -> None:
    document = _feedback_document(tmp_path, bit)
    result_path = _write(tmp_path / "result.json", document)
    (tmp_path / "device.json").unlink()
    (tmp_path / "circuit.qasm3").unlink()

    run = _run("validate", str(result_path))

    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["ok"]
    assert report["semantic"] == "checked"
    assert report["failures"] == []
    assert report["failure_count"] == 0
    assert set(report["checks"]) == {
        "motion",
        "coverage",
        "replay",
        "pulse",
        "addressing",
        "timing",
    }
    assert "device_override" not in report


def test_compile_emits_v2_with_input_snapshots_and_semantic_report(
    tmp_path: Path,
) -> None:
    path = _copied_job(tmp_path)

    run = _run("compile", str(path), "--validate")

    assert run.returncode == 0, run.stderr
    document = json.loads(run.stdout)
    assert document["schema"] == "nadit.result/2"
    assert document["dag"]["nodes"]
    assert document["device"]["slm_sites"]
    assert set(document["inputs"]) == {"qasm_sha256", "device_sha256"}
    assert isinstance(document["provenance"]["git_dirty"], bool)
    assert document["validation"]["semantic"] == "checked"


@pytest.mark.parametrize(
    "tamper,expected",
    [("coverage", "missing_interaction"), ("wait", "feedback_latency")],
)
def test_manual_edits_fail_semantic_validation(
    tmp_path: Path, tamper: str, expected: str
) -> None:
    document = _feedback_document(tmp_path)
    steps = document["program"]["steps"]
    if tamper == "coverage":
        for step in steps:
            for action in step["actions"]:
                if action["kind"] == "CZPulse":
                    action["coverage"] = {
                        "kind": "region",
                        "region": {
                            "x_min": 10000,
                            "y_min": 10000,
                            "x_max": 10001,
                            "y_max": 10001,
                        },
                    }
    else:
        waits = [
            action
            for step in steps
            for action in step["actions"]
            if action["kind"] == "Wait" and action["duration_us"] > 0
        ]
        assert waits
        document["program"]["steps"] = [
            {
                "actions": [
                    action for action in step["actions"] if action["kind"] != "Wait"
                ]
            }
            for step in steps
        ]
    result_path = _write(tmp_path / "tampered.json", document)

    run = _run("validate", str(result_path))

    assert run.returncode == 3
    report = json.loads(run.stdout)
    assert not report["ok"]
    assert report["failure"]["rule"] == expected
    assert expected in {failure["rule"] for failure in report["failures"]}
    assert report["failure_count"] >= 1
    assert "Traceback" not in run.stderr


def test_explicit_device_override_is_used_and_recorded(tmp_path: Path) -> None:
    document = _feedback_document(tmp_path)
    override = deepcopy(document["device"])
    override["cz"]["radius_um"] = 0.1
    override_path = _write(tmp_path / "override.json", override)
    result_path = _write(tmp_path / "result.json", document)

    run = _run("validate", str(result_path), "--device", str(override_path))

    assert run.returncode == 3
    report = json.loads(run.stdout)
    assert report["failure"]["rule"] == "missing_interaction"
    assert report["device_override"] == str(override_path.resolve())


def test_v1_reports_semantic_unavailable_and_accepts_override(tmp_path: Path) -> None:
    document = _feedback_document(tmp_path)
    document["schema"] = "nadit.result/1"
    for field in ("dag", "device", "inputs"):
        document.pop(field)
    document["job"]["device"] = "absent.json"
    result_path = _write(tmp_path / "legacy.json", document)
    device_path = tmp_path / "device.json"

    run = _run("validate", str(result_path), "--device", str(device_path))

    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["semantic"] == "unavailable"
    assert set(report["checks"]) == {"motion", "coverage", "replay"}
    assert report["device_override"] == str(device_path.resolve())


@pytest.mark.parametrize("field", ["dag", "device", "inputs"])
def test_compare_includes_snapshot_fields_after_execution_records(
    tmp_path: Path, field: str
) -> None:
    document = _feedback_document(tmp_path)
    changed = deepcopy(document)
    if field == "dag":
        changed[field]["global_phase"] += 0.5
    elif field == "device":
        changed[field]["timing"]["load_us"] += 1
    else:
        changed[field]["qasm_sha256"] = "0" * 64

    run = _run(
        "compare",
        str(_write(tmp_path / "a.json", document)),
        str(_write(tmp_path / "b.json", changed)),
    )

    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert not report["equal"]
    assert report["first_difference"]["kind"] == field
    assert report["first_difference"]["path"].startswith(field)


def test_compare_v1_and_v2_handles_missing_snapshots(tmp_path: Path) -> None:
    document = _feedback_document(tmp_path)
    legacy = deepcopy(document)
    legacy["schema"] = "nadit.result/1"
    for field in ("dag", "device", "inputs"):
        legacy.pop(field)

    run = _run(
        "compare",
        str(_write(tmp_path / "a.json", legacy)),
        str(_write(tmp_path / "b.json", document)),
    )

    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["first_difference"]["path"] == "dag"
    assert report["first_difference"]["left"] is None
