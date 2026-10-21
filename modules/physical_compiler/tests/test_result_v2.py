"""Portable result snapshots retain the native circuit and exact input identity."""

import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.device import loads_device
from nadit_physical_compiler.job import CompilationJob, compile_job, load_job, loads_job
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    Register,
    ResultRef,
)
from nadit_physical_compiler.serialization import (
    collect_provenance,
    decode_dag,
    decode_layout,
    decode_result,
    encode_dag,
    encode_device,
    encode_result,
)

MODULE = Path(__file__).resolve().parents[1]


def _copied_job(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    spec = json.loads((MODULE / "examples/jobs/pair.json").read_text(encoding="utf-8"))
    spec["circuit"]["qasm"] = "circuit.qasm3"
    spec["device"] = "device.json"
    (directory / "circuit.qasm3").write_bytes(
        (MODULE / "examples/pair_feedback.qasm3").read_bytes()
    )
    (directory / "device.json").write_bytes(
        (MODULE / "configs/one_pair_flexible.json").read_bytes()
    )
    path = directory / "job.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _document(job: CompilationJob) -> dict[str, Any]:
    return encode_result(
        compile_job(job),
        entry_state=job.entry_state,
        job=job.document,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
    )


def test_dag_round_trip_preserves_registers_conditions_and_source_ids() -> None:
    dag = PhysicalDAG(
        3,
        2,
        (
            PhysicalOperation(0, "measure", (1,), (0,), (), source_id=12),
            PhysicalOperation(
                1, "ry", (2,), (), (0.75,), Condition(ResultRef(12), 0), 19
            ),
            PhysicalOperation(2, "global_phase", (), (), (0.5,), source_id=21),
        ),
        ((0, 1),),
        (Register("a", (0, 2)), Register("b", (1,))),
        (Register("readout", (0, 1)),),
        1.25,
    )

    encoded = json.loads(json.dumps(encode_dag(dag), allow_nan=False))

    assert encoded["nodes"][0]["result"] == {"measurement_id": 12}
    assert encoded["nodes"][1]["result"] is None
    assert decode_dag(encoded) == dag
    assert decode_dag(encode_dag(PhysicalDAG(0, 0, (), ()))) == PhysicalDAG(
        0, 0, (), ()
    )


def test_dag_decode_rejects_inconsistent_measurement_identity() -> None:
    dag = PhysicalDAG(
        1, 1, (PhysicalOperation(0, "measure", (0,), (0,), (), source_id=12),), ()
    )
    encoded = encode_dag(dag)
    encoded["nodes"][0]["result"]["measurement_id"] = 99

    with pytest.raises(ValueError, match=r"dag.nodes\[0\].result"):
        decode_dag(encoded)


@pytest.mark.parametrize("bit", [0, 1])
def test_v2_result_round_trip_is_self_contained(tmp_path: Path, bit: int) -> None:
    path = _copied_job(tmp_path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    initial = load_job(path)
    spec["case"]["overrides"] = {
        str(node.result.measurement_id): bit
        for node in initial.dag.nodes
        if node.result is not None
    }
    job = loads_job(json.dumps(spec), base_dir=tmp_path)
    result = compile_job(job)
    document = json.loads(json.dumps(_document(job), allow_nan=False))

    (tmp_path / "circuit.qasm3").unlink()
    (tmp_path / "device.json").unlink()

    assert document["schema"] == "nadit.result/2"
    assert decode_result(document) == result
    assert decode_dag(document["dag"]) == job.dag
    assert loads_device(json.dumps(document["device"])) == job.device
    assert decode_layout(document["entry_state"]) == job.entry_state
    assert document["inputs"] == job.inputs


def test_inputs_and_job_hash_are_independent_of_directory(tmp_path: Path) -> None:
    first = load_job(_copied_job(tmp_path / "first"))
    second = load_job(_copied_job(tmp_path / "second"))

    assert first.document != second.document
    assert first.inputs == second.inputs
    assert (
        _document(first)["provenance"]["job_hash"]
        == _document(second)["provenance"]["job_hash"]
    )


@pytest.mark.parametrize("changed_input", ["qasm", "device", "strategy"])
def test_content_or_strategy_changes_change_job_hash(
    tmp_path: Path, changed_input: str
) -> None:
    path = _copied_job(tmp_path)
    before = load_job(path)
    if changed_input == "qasm":
        source_path = tmp_path / "circuit.qasm3"
        source_path.write_bytes(source_path.read_bytes() + b"\n")
    elif changed_input == "device":
        source_path = tmp_path / "device.json"
        device = json.loads(source_path.read_text(encoding="utf-8"))
        device["timing"]["load_us"] += 1
        source_path.write_text(json.dumps(device), encoding="utf-8")
    else:
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["case"]["seed"] += 1
        path.write_text(json.dumps(spec), encoding="utf-8")
    after = load_job(path)

    first = collect_provenance(before.document, inputs=before.inputs)
    second = collect_provenance(after.document, inputs=after.inputs)

    assert first["job_hash"] != second["job_hash"]
    if changed_input == "strategy":
        assert before.inputs == after.inputs
    else:
        assert (
            before.inputs[f"{changed_input}_sha256"]
            != after.inputs[f"{changed_input}_sha256"]
        )


def test_job_captures_inputs_at_load_time(tmp_path: Path) -> None:
    job = load_job(_copied_job(tmp_path))
    captured = deepcopy(job.inputs)
    original = _document(job)
    (tmp_path / "circuit.qasm3").write_text("changed after load", encoding="utf-8")
    (tmp_path / "device.json").write_text("changed after load", encoding="utf-8")

    document = _document(job)

    assert document["inputs"] == captured
    assert document["provenance"]["job_hash"] == original["provenance"]["job_hash"]
    assert document["dag"] == original["dag"]
    assert document["device"] == original["device"]
    job.inputs["qasm_sha256"] = "changed later"
    assert document["inputs"] == captured


def test_device_hash_uses_expanded_canonical_content(tmp_path: Path) -> None:
    path = _copied_job(tmp_path)
    file_job = load_job(path)
    inline = deepcopy(file_job.document)
    inline["device"] = encode_device(file_job.device)
    inline_job = loads_job(json.dumps(inline), base_dir=tmp_path)
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps(json.loads(device_path.read_text(encoding="utf-8")), indent=4),
        encoding="utf-8",
    )
    reformatted = load_job(path)
    canonical = json.dumps(
        encode_device(file_job.device),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert file_job.inputs["device_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert file_job.inputs == inline_job.inputs == reformatted.inputs
    assert (
        collect_provenance(file_job.document, inputs=file_job.inputs)["job_hash"]
        == collect_provenance(inline_job.document, inputs=inline_job.inputs)["job_hash"]
    )
    assert (
        file_job.inputs["qasm_sha256"]
        == hashlib.sha256((tmp_path / "circuit.qasm3").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("missing", ["dag", "device", "inputs"])
def test_v2_requires_all_snapshot_fields(tmp_path: Path, missing: str) -> None:
    document = _document(load_job(_copied_job(tmp_path)))
    del document[missing]

    with pytest.raises(ValueError, match=rf"schema.*{missing}"):
        decode_result(document)


def test_v2_rejects_incomplete_input_hashes(tmp_path: Path) -> None:
    document = _document(load_job(_copied_job(tmp_path)))
    del document["inputs"]["qasm_sha256"]

    with pytest.raises(ValueError, match="inputs.qasm_sha256"):
        decode_result(document)


@pytest.mark.parametrize("missing", ["dag", "device", "inputs"])
def test_encode_requires_complete_snapshot_arguments(
    tmp_path: Path, missing: str
) -> None:
    job = load_job(_copied_job(tmp_path))
    options: dict[str, Any] = {
        "dag": job.dag,
        "device": job.device,
        "inputs": job.inputs,
    }
    del options[missing]

    with pytest.raises(ValueError, match="dag, device, and inputs"):
        encode_result(
            compile_job(job), entry_state=job.entry_state, job=job.document, **options
        )


def test_git_dirty_tracks_worktree_and_index_but_not_untracked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nadit_physical_compiler import provenance

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", *arguments], cwd=tmp_path, check=True, capture_output=True
        )

    git("init")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("original", encoding="utf-8")
    git("add", "tracked.txt")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    monkeypatch.setattr(provenance, "__file__", str(tmp_path / "provenance.py"))

    assert collect_provenance({})["git_dirty"] is False
    (tmp_path / "untracked.txt").write_text("untracked", encoding="utf-8")
    assert collect_provenance({})["git_dirty"] is False
    tracked.write_text("changed", encoding="utf-8")
    assert collect_provenance({})["git_dirty"] is True
    git("add", "tracked.txt")
    assert collect_provenance({})["git_dirty"] is True


def test_git_unavailable_is_reported_as_null(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(subprocess, "run", unavailable)
    report = collect_provenance({})

    assert report["git_commit"] is None
    assert report["git_dirty"] is None


def test_canonical_hash_rejects_nonfinite_input() -> None:
    with pytest.raises(ValueError, match="JSON"):
        collect_provenance({"nonfinite": float("nan")})


def test_result_v1_remains_available_without_snapshots(tmp_path: Path) -> None:
    job = load_job(_copied_job(tmp_path))
    result = compile_job(job)
    document = encode_result(result, entry_state=job.entry_state, job=job.document)

    assert document["schema"] == "nadit.result/1"
    assert decode_result(document) == result


def test_v2_dag_metadata_does_not_recompile_program(tmp_path: Path) -> None:
    job = load_job(_copied_job(tmp_path))
    result = compile_job(job)
    dag = replace(job.dag, global_phase=0.125)
    document = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        dag=dag,
        device=job.device,
        inputs=job.inputs,
    )

    assert decode_dag(document["dag"]) == dag
    assert decode_result(document).program == result.program
