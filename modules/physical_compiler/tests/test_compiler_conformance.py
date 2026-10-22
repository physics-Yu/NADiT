"""Registration-driven public contracts for built-in and external compilers."""

import json
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest
from test_job import MODULE, spec

from nadit_physical_compiler.compilers.registry import COMPILERS
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.serialization import encode_result
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.validation import validate_result

EXAMPLES = {"search_smarter": "multipair", "nonzoned": "nonzoned-enola-initial"}


@pytest.mark.parametrize("kind", COMPILERS)
@pytest.mark.parametrize("bit", [0, 1])
def test_registered_feedback_determinism_and_identity(kind: str, bit: int) -> None:
    job = load_job(MODULE / f"examples/jobs/{EXAMPLES[kind]}.json")
    document = job.document
    document["case"]["overrides"] = {
        str(n.result.measurement_id): bit for n in job.dag.nodes if n.result
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)
    result = compile_job(job)
    assert result == compile_job(job)
    assert replay(result.program, job.entry_state, job.device) == result.exit_state
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
    encoded = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
    )
    assert encoded["provenance"]["compiler"] == {
        "kind": kind,
        "version": COMPILERS[kind].version,
    }
    assert kind in describe()["compilers"]
    assert job.document["strategy"]["kind"] == kind


@pytest.mark.parametrize("kind", COMPILERS)
def test_default_entry_and_admission_before_build(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = load_job(MODULE / f"examples/jobs/{EXAMPLES[kind]}.json")
    document = job.document
    del document["entry"]
    loaded = loads_job(json.dumps(document), base_dir=MODULE)
    assert loaded.document["entry"]["kind"] == COMPILERS[kind].entry_providers[0]
    document["entry"] = {
        "kind": "enola_initial" if kind == "search_smarter" else "initial_storage"
    }

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("admission must precede construction")

    monkeypatch.setitem(COMPILERS, kind, replace(COMPILERS[kind], build=forbidden))
    with pytest.raises(UnsupportedJob, match="entry"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_unknown_compiler_reports_outer_field() -> None:
    document = spec()
    document["strategy"] = {"kind": "unknown", "scheduler": {}}
    with pytest.raises(ValueError, match="strategy.kind"):
        loads_job(json.dumps(document), base_dir=MODULE)


@pytest.mark.parametrize("kind", COMPILERS)
def test_device_admission_collects_reasons_and_cli_exit_four(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nadit_physical_compiler.cli import main
    from nadit_physical_compiler.serialization import encode_device

    job = load_job(MODULE / f"examples/jobs/{EXAMPLES[kind]}.json")
    document = job.document
    device = encode_device(job.device)
    device["aods"][0]["motion"] = "rigid"
    device["cz"]["holder_pairs"] = [["aod", "aod"]]
    document["device"] = device
    if kind == "search_smarter":
        document["strategy"]["transport"] = {"kind": "relaxed"}

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("factory invoked for an unsupported device")

    monkeypatch.setitem(COMPILERS, kind, replace(COMPILERS[kind], build=forbidden))
    with pytest.raises(UnsupportedJob) as error:
        loads_job(json.dumps(document), base_dir=MODULE)
    assert len(error.value.reasons) >= 2
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(document))
    assert main(["compile", str(path)]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
