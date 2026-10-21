"""Optional pinned-upstream integration; run with --extra cost-hsim."""

import importlib
import json
from math import cos, log, pi
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.cost import evaluate
from nadit_physical_compiler.cost.hsim import HSIM_COMMIT, _backend
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.serialization import encode_result
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]


def document(tmp_path: Path, *, cz_us: float = 0.5) -> dict[str, Any]:
    """Compile a true RX(pi), CZ circuit against the chosen calibrated duration."""
    path = tmp_path / "hsim.qasm"
    path.write_text(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; rx(pi) q[0]; cz q[0],q[1];'
    )
    spec = load_job(MODULE / "examples/jobs/pair.json").document
    spec["circuit"]["qasm"] = str(path)
    device = json.loads(Path(spec["device"]).read_text())
    device["cz"]["duration_us"] = cz_us
    spec["device"] = device
    job = loads_job(json.dumps(spec), base_dir=MODULE)
    result = compile_job(job)
    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"]
    return encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        device=job.device,
        dag=job.dag,
        inputs=job.inputs,
        validation=report,
    )


def test_pinned_upstream_ideal_examples_and_additive_limit(tmp_path: Path) -> None:
    upstream = _backend()
    assert upstream.CZGate(upstream.IdealRydbergLadderModel()).execute().fidelity == 1
    assert (
        upstream.RamanSingleQubitGate.x(upstream.IdealRamanTLSModel())
        .execute()
        .fidelity
        == 1
    )
    result = document(tmp_path)
    hsim = evaluate(result, model="hsim-aqc")
    assert hsim["version"] == HSIM_COMMIT
    assert hsim["metrics"]["log_fidelity"] == pytest.approx(
        evaluate(result)["metrics"]["log_fidelity"]
    )


def test_upstream_overrotation_fixture_matches_closed_form(tmp_path: Path) -> None:
    epsilon = 0.01
    expected = (4 * cos(0.5 * epsilon * pi) ** 2 + 2) / 6
    report = evaluate(
        document(tmp_path),
        model="hsim-aqc",
        params={
            "single": {
                "backend": "coherent_overrotation",
                "parameters": {"fractional_angle_error": epsilon},
            }
        },
    )
    assert report["metrics"]["log_fidelity"] == pytest.approx(log(expected))
    assert report["metrics"]["fidelity"] == pytest.approx(expected)


@pytest.mark.parametrize("backend", ["four_level", "open_four_level"])
def test_cz_backend_matches_direct_upstream_and_checks_duration(
    tmp_path: Path, backend: str
) -> None:
    upstream = importlib.import_module("uftqc")
    pulse = {
        "times_s": [0, 1e-9],
        "omega_1e_amplitudes": [0, 0],
        "omega_1e_phases_rad": [0, 0],
        "omega_er_amplitudes": [0, 0],
        "omega_er_phases_rad": [0, 0],
    }
    model = (
        upstream.FourLevelRydbergCZModel
        if backend == "four_level"
        else upstream.OpenSystemFourLevelRydbergCZModel
    )
    direct = upstream.CZGate(
        model(pulse_profile=upstream.PiecewiseLinearRydbergPulse(**pulse))
    ).execute()
    params = {"cz": {"backend": backend, "pulse": pulse}}
    report = evaluate(
        document(tmp_path, cz_us=direct.duration_s * 1e6),
        model="hsim-aqc",
        params=params,
    )
    assert report["metrics"]["fidelity"] == pytest.approx(direct.fidelity)
    with pytest.raises(ValueError, match="duration"):
        evaluate(document(tmp_path), model="hsim-aqc", params=params)
