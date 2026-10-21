"""Device comparisons separate line capacity from the movement relation."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from examples.compare_motion import MODULE, PROFILES, main, run_study, study_job
from nadit_physical_compiler import load_device
from nadit_physical_compiler.serialization import decode_result, encode_device


def test_comparison_devices_share_every_non_aod_setting() -> None:
    devices = [load_device(MODULE / "configs" / f"{name}.json") for name in PROFILES]
    base = devices[0]
    for device in devices:
        assert replace(device, name=base.name, aods=base.aods) == base
    assert [
        (device.aods[0].motion, device.aods[0].max_rows, device.aods[0].max_columns)
        for device in devices
    ] == [("flexible", None, None), ("flexible", 16, 16), ("rigid", 16, 16)]
    assert len(base.slm_sites) == 128 + 40
    gates = [site.position for site in base.slm_sites if "gate_" in site.id]
    storage = [site.position for site in base.slm_sites if "storage_" in site.id]
    assert len({position.y for position in gates}) == 1
    assert min(point.y for point in gates) - max(point.y for point in storage) > 100


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run_study(results_dir=tmp_path_factory.mktemp("motion-study"))


def test_study_preserves_circuits_feedback_validation_and_saved_results(
    report: dict[str, Any],
) -> None:
    assert len(report["rows"]) == 9
    for case in ("capacity", "feedback_0", "feedback_1"):
        rows = [row for row in report["rows"] if row["case"] == case]
        assert {row["profile"] for row in rows} == set(PROFILES)
        assert len({row["qasm_sha256"] for row in rows}) == 1
        assert (
            len({json.dumps(row["measurements"], sort_keys=True) for row in rows}) == 1
        )
        for row in rows:
            assert row["validation"]["ok"], row["validation"]
            assert row["replay_matches_exit_state"]
            saved = json.loads(Path(row["result"]).read_text(encoding="utf-8"))
            assert saved["schema"] == "nadit.result/2"
            assert saved["device"] == encode_device(
                load_device(MODULE / "configs" / f"{row['profile']}.json")
            )
            assert decode_result(saved).program.steps
            assert saved["metrics"] == row["metrics"]
            if case != "capacity":
                assert list(row["measurements"].values()) == [int(case[-1])]


def test_study_jobs_use_actual_measurement_id_and_requested_policy() -> None:
    job = study_job(
        "zoned_rigid_16",
        "multipair_feedback.qasm3",
        1,
        seed=23,
        placement="multipair",
        transport="sequential",
    )
    assert job.document["case"] == {"seed": 23, "overrides": {"12": 1}}
    assert job.document["strategy"]["placement"]["kind"] == "multipair"
    assert job.document["strategy"]["transport"]["kind"] == "sequential"


def test_study_cli_preserves_report_json(
    report: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("examples.compare_motion.run_study", lambda **kwargs: report)
    destination = tmp_path / "report.json"
    main(["--output", str(destination)])
    assert json.loads(capsys.readouterr().out) == report
    assert json.loads(destination.read_text(encoding="utf-8")) == report
