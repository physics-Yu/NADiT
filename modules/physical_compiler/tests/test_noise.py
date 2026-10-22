"""Analytic physical-noise estimates and validated-result export contracts."""

import json
from copy import deepcopy
from math import exp, prod
from pathlib import Path
from typing import Any

import pytest
from test_cost import cost_document, timeline_document  # noqa: F401

from nadit_physical_compiler.activity import derive_activity
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.cost import UnvalidatedResult, evaluate
from nadit_physical_compiler.cost.noise import NoiseModel
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.experiments.costs import evaluate_models
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.serialization import encode_result
from nadit_physical_compiler.validation import validate_result


def test_transport_loss_multiplies_and_dephasing_uses_transport_time() -> None:
    doc = timeline_document()
    report = NoiseModel().evaluate(
        derive_activity(doc),
        doc,
        {
            "transport": {
                "load_loss": 0.1,
                "store_loss": 0.2,
                "move_loss": 0.05,
                "loss_per_um": 0.03,
                "loss_per_us": 0.01,
            },
            "exposure": {"aod_t2_us": 100, "slm_t2_us": 100},
        },
    )
    survival = 0.9 * 0.8 * 0.95 * exp(-0.03 * 5 - 0.01 * 10)
    loss = report["noise"]["loss"]
    assert loss["per_atom"][0]["survival_probability"] == pytest.approx(survival)
    assert loss["expected_lost_atoms"] == pytest.approx(1 - survival)
    assert loss["all_atoms_survive_probability"] == pytest.approx(survival)
    channels = report["noise"]["channels"]
    dephasing = [c for c in channels if c["instruction"] == "Z_ERROR"]
    # Atom 7: 24 us exposure. Atom 9: 19 us idle, 5 us single gate excluded.
    assert sum(c["duration_us"] for c in dephasing) == 43
    assert all(
        c["probabilities"] == pytest.approx([(1 - exp(-c["duration_us"] / 100)) / 2])
        for c in dephasing
    )
    assert report["metrics"]["fidelity"] == pytest.approx(survival * exp(-43 / 100))
    expected_no_fault = survival * prod(1 - c["probabilities"][0] for c in dephasing)
    assert report["metrics"]["no_fault_probability"] == pytest.approx(expected_no_fault)


def test_holder_loss_covers_busy_and_idle_and_certain_loss_is_json_safe() -> None:
    doc = timeline_document()
    report = NoiseModel().evaluate(
        derive_activity(doc),
        doc,
        {
            "transport": {"aod_loss_per_us": 0.1, "load_loss": 1},
        },
    )
    assert report["noise"]["loss"]["expected_lost_atoms"] == 1
    assert report["metrics"]["fidelity"] == 0
    json.dumps(report, allow_nan=False)
    report = NoiseModel().evaluate(
        derive_activity(doc),
        doc,
        {
            "transport": {"aod_loss_per_us": 0.1},
        },
    )
    # Load completes at 2; store completes at 16. Source holder owns store time.
    assert report["noise"]["loss"]["per_atom"][0][
        "survival_probability"
    ] == pytest.approx(exp(-1.4))


@pytest.mark.parametrize(
    "params",
    [
        {"transport": {"backend": "detailed"}},
        {"transport": {"loss_per_um": -1}},
        {"transport": {"move_loss": 2}},
        {"exposure": {"aod_t2_us": 0}},
        {"single": {"probabilities": [0.5, 0.5, 0.1]}},
        {"cz": {"probabilities": [0.1]}},
        {"typo": 1},
    ],
)
def test_invalid_noise_configuration(params: dict[str, Any]) -> None:
    doc = timeline_document()
    with pytest.raises(ValueError):
        NoiseModel().evaluate(derive_activity(doc), doc, params)


def test_noise_reevaluation_channels_and_cli(
    cost_document: dict[str, Any],  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = deepcopy(cost_document)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Noise evaluation recompiled")

    monkeypatch.setattr("nadit_physical_compiler.job.compile_job", fail)
    params = {
        "cz": {"probabilities": [0.01] + [0.0] * 14},
        "readout_flip": 0.02,
        "reset_flip": 0.03,
    }
    report = evaluate(cost_document, model="noise/v1", params=params)
    assert report == evaluate(cost_document, model="noise/v1", params=params)
    assert cost_document == original
    cz = [
        c for c in report["noise"]["channels"] if c["instruction"] == "PAULI_CHANNEL_2"
    ]
    assert len(cz) == sum(cost_document["metrics"]["cz_gates_per_pulse"])
    assert all(len(c["atoms"]) == len(c["qubits"]) == 2 and c["source_ids"] for c in cz)
    assert all(c["placement"] == "before" for c in cz)
    measurements = [c for c in report["noise"]["channels"] if c["instruction"] == "M"]
    assert len(measurements) == len(cost_document["measurements"])
    assert len({c["measurement_id"] for c in measurements}) == len(measurements)
    assert all(c["placement"] == "measurement_argument" for c in measurements)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(cost_document))
    config = tmp_path / "params.json"
    config.write_text(json.dumps(params))
    assert (
        main(
            [
                "cost",
                str(path),
                "--model",
                "noise/v1",
                "--params",
                str(config),
                "--into",
                str(path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == report
    saved = json.loads(path.read_text())
    assert saved.pop("cost") == [report]
    assert saved == original
    broken = deepcopy(cost_document)
    broken["exit_state"]["atoms"][0]["position"]["x"] += 1
    with pytest.raises(UnvalidatedResult):
        evaluate(broken, model="noise/v1")


def test_experiment_and_discovery_use_same_model(cost_document: dict[str, Any]) -> None:  # noqa: F811
    config = {"model": "noise/v1", "params": {"transport": {"move_loss": 0.001}}}
    report = evaluate_models(cost_document, (config,))[0]
    assert report["status"] == "ok"
    assert report["noise"]["loss"]["expected_lost_atoms"] > 0
    info = describe()["cost_models"]["noise/v1"]
    assert info["transport_backends"] == ["rates/v1"]
    assert "raman_profiled" in info["single_backends"]


def test_idle_only_exposure_and_single_axis_override() -> None:
    doc = timeline_document()
    params = {
        "single": {"probabilities": [0.3, 0, 0]},
        "single_overrides": {"X": {"probabilities": [0, 0.06, 0]}},
        "exposure": {"slm_t2_us": 100, "aod_t2_us": 100, "include_transport": False},
    }
    report = NoiseModel().evaluate(derive_activity(doc), doc, params)
    channels = report["noise"]["channels"]
    single = next(c for c in channels if c["instruction"] == "PAULI_CHANNEL_1")
    assert single["probabilities"] == [0, 0.06, 0]
    assert single["atoms"] == [9] and single["qubits"] == [1]
    assert report["metrics"]["fidelity"] == pytest.approx(0.96 * exp(-0.3))


@pytest.mark.parametrize("bit", [0, 1])
def test_noise_preserves_actual_feedback_branch(bit: int) -> None:
    module = Path(__file__).resolve().parents[1]
    template = load_job(module / "examples/jobs/multipair.json")
    spec = template.document
    spec["case"]["overrides"] = {
        str(node.result.measurement_id): bit
        for node in template.dag.nodes
        if node.result is not None
    }
    job = loads_job(json.dumps(spec), base_dir=module)
    execution = compile_job(job)
    validation = validate_result(
        execution, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert validation["ok"]
    doc = encode_result(
        execution,
        entry_state=job.entry_state,
        job=job.document,
        device=job.device,
        dag=job.dag,
        inputs=job.inputs,
        validation=validation,
    )
    original = deepcopy(doc)
    report = evaluate(
        doc,
        model="noise/v1",
        params={"readout_flip": 1, "cz": {"probabilities": [0.01] + [0] * 14}},
    )
    channels = report["noise"]["channels"]
    assert sum(c["instruction"] == "PAULI_CHANNEL_2" for c in channels) == 4 + 2 * bit
    assert report["metrics"]["no_fault_probability"] == 0
    assert report["metrics"]["fidelity"] == 0
    assert doc == original
    json.dumps(report, allow_nan=False)
