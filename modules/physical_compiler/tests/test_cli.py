"""The CLI consumes saved jobs and results through subprocess boundaries."""

import json
import subprocess
import sys
import sysconfig
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from test_job import MODULE, spec

from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.serialization import encode_result


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "nadit_physical_compiler.cli", *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=MODULE,
    )


def _write(path: Path, document: dict[str, Any]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def job_document() -> dict[str, Any]:
    document = spec()
    document["circuit"]["qasm"] = str(MODULE / document["circuit"]["qasm"])
    document["device"] = str(MODULE / document["device"])
    return document


@pytest.fixture
def result_document(job_document: dict[str, Any]) -> dict[str, Any]:
    job = loads_job(json.dumps(job_document), base_dir=MODULE)
    return encode_result(
        compile_job(job), entry_state=job.entry_state, job=job.document, provenance={}
    )


def test_compile_writes_json_text_and_validation(
    tmp_path: Path, job_document: dict[str, Any]
) -> None:
    job_path = _write(tmp_path / "job.json", job_document)
    output = tmp_path / "result.json"
    text = tmp_path / "program.txt"
    run = _run(
        "compile", str(job_path), "-o", str(output), "--text", str(text), "--validate"
    )
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    document = json.loads(run.stdout)
    assert document == json.loads(output.read_text(encoding="utf-8"))
    assert document["schema"] == "nadit.result/2"
    assert document["validation"] == {
        "ok": True,
        "failure": None,
        "failures": [],
        "failure_count": 0,
        "checks": ["motion", "coverage", "replay", "pulse", "addressing", "timing"],
        "semantic": "checked",
    }
    assert document["metrics"]["atom_steps"] > 0
    assert "CZ_PULSE(" in text.read_text(encoding="utf-8")


def test_validate_does_not_read_circuit(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    result_document["job"]["circuit"]["qasm"] = str(tmp_path / "absent.qasm3")
    path = _write(tmp_path / "result.json", result_document)
    run = _run("validate", str(path))
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "ok": True,
        "failure": None,
        "failures": [],
        "failure_count": 0,
        "checks": ["motion", "coverage", "replay"],
        "semantic": "unavailable",
    }


def test_validate_reports_real_exit_state_failure(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    result_document["exit_state"]["atoms"][0]["position"]["x"] += 1
    path = _write(tmp_path / "invalid.json", result_document)
    run = _run("validate", str(path))
    assert run.returncode == 3
    report = json.loads(run.stdout)
    assert not report["ok"]
    assert report["failure"]["rule"] == "exit_state_mismatch"
    assert "validation" in run.stderr
    assert "Traceback" not in run.stderr


@pytest.mark.parametrize(
    "arguments", [(), ("unknown",), ("compile",), ("validate", "--bad")]
)
def test_argument_errors_return_one(arguments: tuple[str, ...]) -> None:
    run = _run(*arguments)
    assert run.returncode == 1
    assert run.stdout == ""
    assert run.stderr
    assert "Traceback" not in run.stderr


@pytest.mark.parametrize("source", ["{", "[]", '{"schema": "nadit.result/99"}'])
def test_invalid_result_documents_return_one(tmp_path: Path, source: str) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(source, encoding="utf-8")
    run = _run("validate", str(path))
    assert run.returncode == 1
    assert run.stdout == ""
    assert run.stderr
    assert "Traceback" not in run.stderr


def test_missing_input_returns_one(tmp_path: Path) -> None:
    run = _run("compile", str(tmp_path / "absent.json"))
    assert run.returncode == 1
    assert "absent.json" in run.stderr


def test_unknown_action_returns_one(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    result_document["program"]["steps"][0]["actions"][0]["kind"] = "Teleport"
    path = _write(tmp_path / "invalid.json", result_document)
    run = _run("validate", str(path))
    assert run.returncode == 1
    assert "Teleport" in run.stderr


@pytest.mark.parametrize("malformed", ["missing_steps", "null_action"])
def test_malformed_result_fields_return_one(
    tmp_path: Path, result_document: dict[str, Any], malformed: str
) -> None:
    if malformed == "missing_steps":
        del result_document["program"]["steps"]
    else:
        result_document["program"]["steps"][0]["actions"][0] = None
    path = _write(tmp_path / "invalid.json", result_document)
    run = _run("validate", str(path))
    assert run.returncode == 1
    assert run.stdout == ""
    assert "Traceback" not in run.stderr


def test_validate_accepts_saved_inline_device(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    result_document["job"]["device"] = json.loads(
        Path(result_document["job"]["device"]).read_text(encoding="utf-8")
    )
    path = _write(tmp_path / "result.json", result_document)
    run = _run("validate", str(path))
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["ok"]


def test_real_compile_failure_returns_two(
    tmp_path: Path, job_document: dict[str, Any]
) -> None:
    device = json.loads(Path(job_document["device"]).read_text(encoding="utf-8"))
    device["local"] = None
    job_document["device"] = device
    path = _write(tmp_path / "job.json", job_document)
    run = _run("compile", str(path))
    assert run.returncode == 2
    assert run.stdout == ""
    assert "A local-control capability is required." in run.stderr
    assert "Traceback" not in run.stderr


def test_compare_equal_execution_ignores_job_and_provenance(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    first = _write(tmp_path / "a.json", result_document)
    second_document = deepcopy(result_document)
    second_document["job"]["case"]["seed"] = 999
    second_document["provenance"] = {"git_commit": "another"}
    second = _write(tmp_path / "b.json", second_document)
    run = _run("compare", str(first), str(second))
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "equal": True,
        "metrics": {},
        "first_difference": None,
    }


def test_compare_changed_option_locates_action_and_metrics(
    tmp_path: Path, job_document: dict[str, Any], result_document: dict[str, Any]
) -> None:
    first = _write(tmp_path / "a.json", result_document)
    job_document["strategy"]["transport"] = {"kind": "strict"}
    job = loads_job(json.dumps(job_document), base_dir=MODULE)
    second = _write(
        tmp_path / "b.json",
        encode_result(
            compile_job(job),
            entry_state=job.entry_state,
            job=job.document,
            provenance={},
        ),
    )
    run = _run("compare", str(first), str(second))
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert not report["equal"]
    assert report["metrics"]["transport_duration_us"]["delta"] != 0
    assert report["first_difference"]["kind"] == "action"
    assert report["first_difference"]["step"] == 3
    assert report["first_difference"]["action"] == 0
    assert (
        report["first_difference"]["path"]
        == "program.steps[3].actions[0].grid.columns[1]"
    )


@pytest.mark.parametrize(
    "field", ["measurements", "entry_state", "exit_state", "operations"]
)
def test_compare_result_records_prevent_false_equality(
    tmp_path: Path, result_document: dict[str, Any], field: str
) -> None:
    first = _write(tmp_path / "a.json", result_document)
    second_document = deepcopy(result_document)
    if field == "measurements":
        second_document[field][0]["value"] ^= 1
    elif field == "operations":
        second_document[field][0]["end_us"] += 1
    else:
        second_document[field]["atoms"][0]["position"]["x"] += 1
    second = _write(tmp_path / "b.json", second_document)
    report = json.loads(_run("compare", str(first), str(second)).stdout)
    assert not report["equal"]
    assert report["first_difference"]["path"].startswith(field)


def test_compare_common_prefix_before_list_length(
    tmp_path: Path, result_document: dict[str, Any]
) -> None:
    first = _write(tmp_path / "a.json", result_document)
    second_document = deepcopy(result_document)
    actions = second_document["program"]["steps"][0]["actions"]
    actions[0]["duration_us"] += 1
    actions.append({"kind": "Wait", "duration_us": 1, "source_ids": []})
    second = _write(tmp_path / "b.json", second_document)
    report = json.loads(_run("compare", str(first), str(second)).stdout)
    assert (
        report["first_difference"]["path"] == "program.steps[0].actions[0].duration_us"
    )


@pytest.mark.parametrize("field", ["layer", "step", "phase"])
def test_compare_locates_layer_step_and_global_phase(
    tmp_path: Path, result_document: dict[str, Any], field: str
) -> None:
    first = _write(tmp_path / "a.json", result_document)
    second_document = deepcopy(result_document)
    if field == "layer":
        second_document["layers"][0]["operation_ids"][0] = 999
        expected_path, expected_kind = "layers[0].operation_ids[0]", "layer"
    elif field == "step":
        steps = second_document["program"]["steps"]
        expected_path, expected_kind = f"program.steps[{len(steps)}]", "step"
        steps.append({"actions": []})
    else:
        second_document["program"]["global_phase"] += 0.5
        expected_path, expected_kind = "program.global_phase", "program"
    second = _write(tmp_path / "b.json", second_document)
    run = _run("compare", str(first), str(second))
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["first_difference"]["path"] == expected_path
    assert report["first_difference"]["kind"] == expected_kind


def test_installed_console_script() -> None:
    executable = Path(sysconfig.get_path("scripts")) / (
        "nadit.exe" if sys.platform == "win32" else "nadit"
    )
    run = subprocess.run(
        [str(executable), "--help"], capture_output=True, text=True, check=False
    )
    assert run.returncode == 0
    assert "compile" in run.stdout
    assert "compare" in run.stdout
