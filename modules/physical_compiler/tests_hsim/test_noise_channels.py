"""Compare exported channels with the pinned upstream models, not gate counts."""

from dataclasses import replace
from math import pi, sin
from pathlib import Path
from typing import Any

import pytest
from test_adapter import document

from nadit_physical_compiler.activity import ActivityEvent
from nadit_physical_compiler.cost import evaluate
from nadit_physical_compiler.cost.hsim import _backend
from nadit_physical_compiler.cost.noise.config import gate_config
from nadit_physical_compiler.cost.noise.gates import GateChannels


def rotation(angle: float = pi, duration_us: float = 1.0) -> ActivityEvent:
    """A native RX exposure with independently controlled angle and duration."""
    return ActivityEvent(
        "Rotation",
        0,
        duration_us,
        (0,),
        ((0, 0),),
        ((0, 0),),
        parameters={"axis": "X", "angle": angle},
    )


def test_overrotation_twirl_and_cache() -> None:
    adapter = GateChannels()
    config = gate_config(
        {
            "backend": "coherent_overrotation",
            "parameters": {"fractional_angle_error": 0.1},
        }
    )
    event = rotation()
    effect = adapter.evaluate(config, event)
    assert effect.pauli == pytest.approx((sin(0.05 * pi) ** 2, 0, 0))
    assert adapter.evaluate(config, replace(event, atoms=(9,), start_us=100)) is effect
    assert len(adapter.cache) == 1
    assert effect.computational_survival == 1


def test_magnetic_detuning_is_in_before_gate_frame() -> None:
    event = rotation(pi / 2, 1)
    config = gate_config(
        {
            "backend": "magnetic_detuning",
            "bz_t": 0.001,
            "parameters": {"magnetic_sensitivity_hz_per_t": 1e8},
        }
    )
    effect = GateChannels().evaluate(config, event)
    # Z after RX(pi/2) becomes Y before the ideal gate.
    assert effect.pauli == pytest.approx((0, sin(0.1 * pi) ** 2, 0), abs=1e-12)
    assert effect.diagnostics["phase_rad"] == pytest.approx(0.2 * pi)


@pytest.mark.parametrize("profiled", [False, True])
def test_raman_scattering_channels_match_direct_upstream(profiled: bool) -> None:
    upstream = _backend()
    calibration = {
        "two_photon_rabi_hz_at_peak": 500_000,
        "differential_light_shift_hz_at_peak": 1000,
        "scattering_rate_per_s_at_peak": 2000,
    }
    config: dict[str, Any] = {
        "backend": "raman_profiled" if profiled else "raman_calibrated",
        "calibration": calibration,
    }
    kwargs: dict[str, Any] = {
        "calibration": upstream.RamanAtomicCalibration(**calibration)
    }
    if profiled:
        pulse = {
            "times_s": [0, 1e-6],
            "normalized_intensities": [1, 1],
            "phases_rad": [0, 0],
        }
        config["pulse"] = pulse
        direct_model = upstream.ProfiledRamanAdiabaticEliminationModel(
            **kwargs, pulse_profile=upstream.PiecewiseLinearRamanPulse(**pulse)
        )
    else:
        direct_model = upstream.RamanAdiabaticEliminationModel(**kwargs)
    direct = upstream.RamanSingleQubitGate.rx(pi, direct_model).execute()
    effect = GateChannels().evaluate(gate_config(config), rotation())
    assert effect.pauli == pytest.approx(
        direct.error_model.parameters["stim_pauli_channel_1"]
    )
    assert effect.fidelity == pytest.approx(direct.fidelity)
    assert effect.diagnostics["scattering_probability"] > 0
    with pytest.raises(ValueError, match="duration"):
        GateChannels().evaluate(gate_config(config), rotation(duration_us=2))


@pytest.mark.parametrize("backend", ["four_level", "open_four_level"])
def test_cz_channels_leakage_and_no_double_count(tmp_path: Path, backend: str) -> None:
    upstream = _backend()
    pulse = {
        "times_s": [0, 1e-9],
        "omega_1e_amplitudes": [0.2, 0.2],
        "omega_1e_phases_rad": [0, 0],
        "omega_er_amplitudes": [0.2, 0.2],
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
    result = document(tmp_path, cz_us=direct.duration_s * 1e6)
    report = evaluate(
        result, model="noise/v1", params={"cz": {"backend": backend, "pulse": pulse}}
    )
    channel = next(
        c for c in report["noise"]["channels"] if c["instruction"] == "PAULI_CHANNEL_2"
    )
    weights = direct.error_model.parameters["stim_pauli_channel_2"]
    survival = 1 - direct.error_model.parameters["stim_erasure_probability"]
    assert channel["probabilities"] == pytest.approx(weights)
    assert report["noise"]["gate_diagnostics"][0][
        "computational_survival_probability"
    ] == pytest.approx(survival)
    assert report["noise"]["loss"]["expected_lost_atoms"] == 0
    assert report["metrics"]["fidelity"] == pytest.approx(direct.fidelity)
    assert report["metrics"]["no_fault_probability"] == pytest.approx(
        (1 - sum(weights)) * survival
    )
    assert report["noise"]["gate_diagnostics"][0]["virtual_z_correction_applied"]
