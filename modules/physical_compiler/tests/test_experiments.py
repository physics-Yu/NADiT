"""Fixed inputs, sweep identities, process outcomes, resume, and noise reevaluation."""

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.experiments.costs import reevaluate
from nadit_physical_compiler.experiments.journal import directory_lock, read_records
from nadit_physical_compiler.experiments.runner import run_experiment
from nadit_physical_compiler.experiments.spec import load_spec, plan, runs
from nadit_physical_compiler.experiments.suite import load_suite
from nadit_physical_compiler.experiments.sweep import expand
from nadit_physical_compiler.experiments.version import check_version, code_version

MODULE = Path(__file__).resolve().parents[1]


def experiment_file(tmp_path: Path) -> Path:
    """Create a small real circuit/spec, independent of ignored paper inputs."""
    qasm = b'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; rx(0.3) q[0]; cz q[0],q[1];'
    (tmp_path / "input.qasm").write_bytes(qasm)
    suite = {
        "schema": "nadit.suite/1",
        "name": "test",
        "gate_set": ["rx", "ry", "rz", "cz"],
        "circuits": [
            {
                "id": "two",
                "file": "input.qasm",
                "sha256": hashlib.sha256(qasm).hexdigest(),
                "qubits": 2,
            }
        ],
        "source": "test",
    }
    (tmp_path / "suite.json").write_text(json.dumps(suite))
    params = json.loads(
        (MODULE / "examples/architectures/multipair_flexible.json").read_text()
    )["params"]
    strategy = json.loads((MODULE / "examples/experiments/ids_strict.json").read_text())
    spec = {
        "schema": "nadit.experiment/1",
        "name": "test",
        "suite": "suite.json",
        "architectures": [{"family": "zoned/v1", "params": params}],
        "strategies": {"ids": strategy},
        "cases": {"seeds": [1]},
        "cost": [{"model": "additive", "params": {"cz_infidelity": 0.01}}],
        "budget": {"timeout_s": 30},
        "validate": "required",
        "store": "all",
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(spec))
    return path


def change(path: Path, callback: Any) -> None:
    document = json.loads(path.read_text())
    callback(document)
    path.write_text(json.dumps(document))


def test_product_zip_exclude_and_invalid_axes() -> None:
    base: dict[str, Any] = {
        "aod": {"motion": "flexible", "max_rows": None},
        "gates": [{"columns": 2}],
    }
    sweep: dict[str, list[Any]] = {
        "aod.motion": ["flexible", "rigid"],
        "aod.max_rows": [None, 16],
        "gates[0].columns": [2, 4, 8],
    }
    assert len(list(expand(base, sweep, [], []))) == 12
    assert len(list(expand(base, sweep, [["aod.motion", "aod.max_rows"]], []))) == 6
    assert (
        len(
            list(
                expand(
                    base,
                    sweep,
                    [["aod.motion", "aod.max_rows"]],
                    [{"aod.motion": "rigid"}],
                )
            )
        )
        == 3
    )
    assert base["aod"]["motion"] == "flexible"
    with pytest.raises(ValueError, match="length"):
        list(expand(base, sweep, [["aod.motion", "gates[0].columns"]], []))
    with pytest.raises(ValueError, match="distinct"):
        list(expand(base, sweep, [["aod.motion", "aod.motion"]], []))


def test_suite_hash_and_run_identity_ignore_noise_but_include_code_and_budget(
    tmp_path: Path,
) -> None:
    path = experiment_file(tmp_path)
    spec = load_spec(path)
    version = {"commit": "a", "dirty": False, "source_hash": "b"}
    original = runs(spec, version)[0]["run_id"]
    change(path, lambda doc: doc["cost"][0]["params"].update(cz_infidelity=0.2))
    assert runs(load_spec(path), version)[0]["run_id"] == original
    assert runs(spec, {**version, "source_hash": "c"})[0]["run_id"] != original
    change(path, lambda doc: doc["budget"].update(timeout_s=20))
    assert runs(load_spec(path), version)[0]["run_id"] != original
    (tmp_path / "input.qasm").write_text("changed")
    with pytest.raises(ValueError, match="changed input"):
        load_suite("suite.json", tmp_path)


def test_plan_admits_without_compiling_and_reports_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = experiment_file(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("plan invoked compile_job")

    monkeypatch.setattr("nadit_physical_compiler.job.compile_job", fail)
    version = code_version(allow_dirty=True)
    assert plan(load_spec(path), version)["rejected"] == []
    change(
        path,
        lambda doc: doc["architectures"][0]["params"]["aod"].update(motion="rigid"),
    )
    change(
        path, lambda doc: doc["strategies"]["ids"].update(transport={"kind": "staged"})
    )
    report = plan(load_spec(path), version)
    assert report["runs"] == 1
    assert report["rejected"][0]["status"] == "unsupported"


def test_real_process_resume_and_cost_only_reevaluation(tmp_path: Path) -> None:
    path = experiment_file(tmp_path)
    output = tmp_path / "runs"
    first = run_experiment(load_spec(path), output, allow_dirty=True)
    assert first["statuses"] == {"ok": 1}
    record = read_records(output / "runs.jsonl")[0]
    assert record["validation"]["ok"]
    assert record["cost"][0]["status"] == "ok"
    assert record["compile"]["wall_s"] >= 0
    assert record["hardware"]["gate_pairs"] == 2
    content = (output / "runs.jsonl").read_bytes()
    with (output / "runs.jsonl").open("ab") as stream:
        stream.write(b'{"run_id":')
    assert run_experiment(load_spec(path), output, allow_dirty=True)["completed"] == 1
    assert (output / "runs.jsonl").read_bytes() == content
    costs = ({"model": "additive", "params": {"cz_infidelity": 0.2}},)
    assert reevaluate(output, costs)["evaluated"] == 1
    assert reevaluate(output, costs)["evaluated"] == 0
    assert (output / "runs.jsonl").read_bytes() == content
    assert (
        read_records(output / "costs.jsonl")[0]["cost"]["metrics"]["fidelity"]
        < record["cost"][0]["metrics"]["fidelity"]
    )


@pytest.mark.parametrize(
    "outcome", ["unsupported", "invalid_job", "compile_error", "timeout"]
)
def test_worker_outcomes_are_records_and_not_retried(
    tmp_path: Path, outcome: str
) -> None:
    path = experiment_file(tmp_path)
    if outcome == "unsupported":
        change(
            path,
            lambda doc: doc["architectures"][0]["params"]["aod"].update(motion="rigid"),
        )
        change(
            path,
            lambda doc: doc["strategies"]["ids"].update(transport={"kind": "relaxed"}),
        )
    elif outcome == "invalid_job":
        change(path, lambda doc: doc["cases"].update(overrides={"99": 1}))
    elif outcome == "compile_error":
        change(path, lambda doc: doc["architectures"][0]["params"].update(local=None))
    else:
        change(path, lambda doc: doc["budget"].update(timeout_s=0.001))
    output = tmp_path / "runs"
    result = run_experiment(load_spec(path), output, allow_dirty=True)
    assert result["statuses"] == {outcome: 1}
    assert run_experiment(load_spec(path), output, allow_dirty=True)["completed"] == 1
    assert len(read_records(output / "runs.jsonl")) == 1


def test_validation_failure_is_persisted_with_full_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = experiment_file(tmp_path)
    script = tmp_path / "invalid_worker.py"
    script.write_text("""import json,sys
from pathlib import Path
from nadit_physical_compiler.experiments import worker
from nadit_physical_compiler.experiments.journal import write_json
worker.validate_result = lambda *a, **kw: {
    "ok": False, "failure": {"rule":"fixture","message":"invalid result"}}
write_json(Path(sys.argv[2]), worker.execute(json.loads(Path(sys.argv[1]).read_text())))
""")
    original = subprocess.Popen

    def launch(command: Any, **kwargs: Any) -> Any:
        if len(command) > 2 and command[1:3] == [
            "-m",
            "nadit_physical_compiler.experiments.worker",
        ]:
            command = [command[0], str(script), *command[3:]]
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    output = tmp_path / "runs"
    assert run_experiment(load_spec(path), output, allow_dirty=True)["statuses"] == {
        "validation_failed": 1
    }
    row = read_records(output / "runs.jsonl")[0]
    assert row["cost"] == []
    assert (output / row["provenance"]["result_path"]).exists()


def test_version_changes_and_second_writer_are_rejected(tmp_path: Path) -> None:
    version = code_version(allow_dirty=True)
    with pytest.raises(ValueError, match="Code changed"):
        check_version({**version, "source_hash": "changed"})
    with directory_lock(tmp_path):
        with pytest.raises(ValueError, match="writer"):
            with directory_lock(tmp_path):
                pass


def test_completed_manifest_cannot_silently_mix_versions(tmp_path: Path) -> None:
    path = experiment_file(tmp_path)
    output = tmp_path / "runs"
    run_experiment(load_spec(path), output, allow_dirty=True)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["code"]["source_hash"] = "different"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="new-version"):
        run_experiment(load_spec(path), output, allow_dirty=True)


def test_slow_parent_cost_does_not_hide_worker_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nadit_physical_compiler.experiments.worker import execute

    path = experiment_file(tmp_path)
    change(path, lambda doc: doc["cases"].update(seeds=[0, 1]))
    change(path, lambda doc: doc["budget"].update(timeout_s=1))
    spec = load_spec(path)
    expanded = runs(spec, code_version(allow_dirty=True))
    fixture = tmp_path / "completed.json"
    fixture.write_text(json.dumps(execute(expanded[0])))
    script = tmp_path / "timed_worker.py"
    script.write_text("""import json,sys,time
from pathlib import Path
run=json.loads(Path(sys.argv[1]).read_text())
if run["case"]["seed"] == 1:
    time.sleep(2)
record=json.loads(Path(sys.argv[3]).read_text())
record.update({k:v for k,v in run.items() if k != "job"})
Path(sys.argv[2]).write_text(json.dumps(record))
""")
    original = subprocess.Popen

    def launch(command: Any, **kwargs: Any) -> Any:
        if len(command) > 2 and command[1:3] == [
            "-m",
            "nadit_physical_compiler.experiments.worker",
        ]:
            command = [command[0], str(script), *command[3:], str(fixture)]
        return original(command, **kwargs)

    def slow_cost(*args: Any) -> list[dict[str, Any]]:
        time.sleep(3)
        return []

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(
        "nadit_physical_compiler.experiments.runner.evaluate_models", slow_cost
    )
    report = run_experiment(spec, tmp_path / "runs", workers=2, allow_dirty=True)
    assert report["statuses"] == {"timeout": 1, "ok": 1}
