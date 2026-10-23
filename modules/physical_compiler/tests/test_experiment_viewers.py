"""Anomaly exports use the packaged renderer and report unavailable results."""

import json
from pathlib import Path

from test_experiment_summary import row
from test_view_cli import saved

from nadit_physical_compiler.cli import main
from nadit_physical_compiler.experiments.journal import append, write_json
from nadit_physical_compiler.experiments.summary import summarize


def test_summary_exports_available_anomalies_and_continues_after_bad_results(
    tmp_path: Path,
) -> None:
    result = saved(tmp_path)
    write_json(tmp_path / "experiment.json", {"cost": []})
    bad = tmp_path / "invalid.json"
    bad.write_text('{"schema":"unknown"}', encoding="utf-8")
    for name, source in (
        ("valid", result.name),
        ("absent", "missing.json"),
        ("unsaved", None),
        ("invalid", bad.name),
    ):
        record = row(name, "flexible", "circuit", 10)
        record["compile"]["budget_hit"] = True
        record["provenance"]["result_path"] = source
        append(tmp_path / "runs.jsonl", record)
    default = summarize(tmp_path)
    assert not (tmp_path / "viewers").exists()
    command = next(r for r in default["outliers"] if r["run_id"] == "valid")
    assert command["viewer_command"].startswith("nadit view ")
    assert "examples" not in command["viewer_command"]
    assert command["viewer_argv"][:2] == ["nadit", "view"]
    assert main(["experiment", "summarize", str(tmp_path), "--viewers"]) == 0
    report = json.loads((tmp_path / "summary.json").read_text())
    viewers = {r["run_id"]: r["viewer"] for r in report["outliers"]}
    assert viewers["valid"]["status"] == "written"
    assert Path(viewers["valid"]["path"]).is_file()
    assert viewers["absent"]["status"] == "missing_result"
    assert viewers["unsaved"]["status"] == "not_saved"
    assert viewers["invalid"]["status"] == "error"
    assert viewers["invalid"]["reason"]
    before = Path(viewers["valid"]["path"]).read_bytes()
    summarize(tmp_path, viewers=True)
    assert Path(viewers["valid"]["path"]).read_bytes() == before
