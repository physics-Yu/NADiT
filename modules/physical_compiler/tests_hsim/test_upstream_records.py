"""Preserve non-Pauli information and state-dependent computational leakage."""

import importlib
import json
from dataclasses import fields, replace
from math import pi
from pathlib import Path
from typing import Any

import pytest
from test_adapter import document
from test_noise_channels import rotation

from nadit_physical_compiler.cost import evaluate
from nadit_physical_compiler.cost.cli import cost_result
from nadit_physical_compiler.cost.hsim import _backend
from nadit_physical_compiler.cost.noise.config import gate_config
from nadit_physical_compiler.cost.noise.gates import GateChannels
from nadit_physical_compiler.cost.noise.native import encode_native


@pytest.mark.parametrize("backend", ["four_level", "open_four_level"])
def test_cz_native_channel_survives_json_with_state_dependent_leakage(
    backend: str,
) -> None:
    uftqc = _backend()
    np = importlib.import_module("numpy")
    pulse = {
        "times_s": [0, 1e-9],
        "omega_1e_amplitudes": [0.2, 0.2],
        "omega_1e_phases_rad": [0, 0],
        "omega_er_amplitudes": [0.2, 0.2],
        "omega_er_phases_rad": [0, 0],
    }
    constructor = (
        uftqc.FourLevelRydbergCZModel
        if backend == "four_level"
        else uftqc.OpenSystemFourLevelRydbergCZModel
    )
    direct = uftqc.CZGate(
        constructor(pulse_profile=uftqc.PiecewiseLinearRydbergPulse(**pulse))
    ).execute()
    event = replace(
        rotation(),
        kind="CZPulse",
        duration_us=0.001,
        atoms=(7, 9),
        pairs=((7, 9),),
        parameters={},
    )
    adapter = GateChannels()
    effect = adapter.evaluate(
        gate_config({"backend": backend, "pulse": pulse}, cz=True), event
    )
    records = json.loads(json.dumps(adapter.records, allow_nan=False))
    native = records[effect.record_id]["result"]
    assert set(native) == {"type", *(f.name for f in fields(direct))}
    assert native["error_model"]["parameters"] == encode_native(
        direct.error_model.parameters
    )
    assert native["diagnostics"] == encode_native(direct.diagnostics)
    assert native["validity_domain"] == encode_native(direct.validity_domain)
    assert native["uncertainty"] == encode_native(direct.uncertainty)
    assert native["provenance"] == encode_native(direct.provenance)
    action = native["quantum_instrument"]["action"]
    assert action["type"] == "KrausAction"
    operators = [
        np.array(k["real"]) + 1j * np.array(k["imag"])
        for k in action["kraus_operators"]
    ]
    # A nonuniform input exposes state-dependent trace loss that one average
    # leakage probability cannot preserve. Rebuild the native channel from JSON.
    rho = np.diag([0.1, 0.2, 0.3, 0.4]).astype(complex)
    restored = sum(k @ rho @ k.conj().T for k in operators)
    expected = direct.quantum_instrument.action.apply(rho)
    assert np.allclose(restored, expected, atol=1e-12, rtol=0)
    average_survival = sum(float(np.trace(k.conj().T @ k).real) for k in operators) / 4
    assert average_survival == pytest.approx(effect.computational_survival)
    assert native["quantum_instrument"]["back_action"]["type"] == "IdentityBackAction"


@pytest.mark.parametrize(
    "backend",
    [
        "coherent_overrotation",
        "magnetic_detuning",
        "raman_calibrated",
        "raman_profiled",
    ],
)
def test_single_native_records_include_every_upstream_field_and_deduplicate(
    backend: str,
) -> None:
    config: dict[str, Any] = {"backend": backend}
    if backend == "coherent_overrotation":
        config["parameters"] = {"fractional_angle_error": 0.1}
    elif backend == "magnetic_detuning":
        config.update(bz_t=0.001, parameters={"magnetic_sensitivity_hz_per_t": 1e8})
    else:
        config["calibration"] = {
            "two_photon_rabi_hz_at_peak": 500_000,
            "differential_light_shift_hz_at_peak": 1000,
            "scattering_rate_per_s_at_peak": 2000,
        }
        if backend == "raman_profiled":
            config["pulse"] = {
                "times_s": [0, 1e-6],
                "normalized_intensities": [1, 1],
                "phases_rad": [0, 0],
            }
    adapter = GateChannels()
    event = rotation(pi, 1)
    effect = adapter.evaluate(gate_config(config), event)
    assert (
        adapter.evaluate(gate_config(config), replace(event, atoms=(999,), start_us=20))
        is effect
    )
    assert len(adapter.records) == 1
    native = json.loads(json.dumps(adapter.records, allow_nan=False))[effect.record_id][
        "result"
    ]
    assert native["coherent_unitary"]["shape"] == [2, 2]
    assert native["physical_observables"] is not None
    assert native["validity_domain"] and native["provenance"] and native["uncertainty"]
    if backend.startswith("raman_"):
        assert native["quantum_instrument"]["action"]["type"] == "SuperoperatorAction"
        assert "unmodeled_scattering_effects" in native["error_model"]["parameters"]
    else:
        assert native["quantum_instrument"]["action"]["type"] == "UnitaryAction"


def test_native_export_references_and_revision_do_not_overwrite_old_report(
    tmp_path: Path,
) -> None:
    doc = document(tmp_path)
    params = {
        "single": {
            "backend": "coherent_overrotation",
            "parameters": {"fractional_angle_error": 0.1},
        }
    }
    report = evaluate(doc, model="noise/v1", params=params)
    assert report["version"] == "v1.1"
    native = report["noise"]["upstream_records"]
    channels = [c for c in report["noise"]["channels"] if "upstream_record_id" in c]
    assert channels and all(c["upstream_record_id"] in native for c in channels)
    assert all(
        g["upstream_record_id"] in native for g in report["noise"]["gate_diagnostics"]
    )
    old = {**report, "version": "v1"}
    doc["cost"] = [old]
    path = tmp_path / "result.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params), encoding="utf-8")
    cost_result(path, model="noise/v1", params_path=params_path, into=path)
    saved = json.loads(path.read_text())
    assert [r["version"] for r in saved["cost"]] == ["v1", "v1.1"]
    assert saved["cost"][0] == old
    assert saved["cost"][1] == report
