"""Installed CLI viewing consumes saved data, never compiler/example inputs."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_result_v2 import _copied_job, _document

from nadit_physical_compiler.cli import main
from nadit_physical_compiler.job import load_job


def saved(tmp_path: Path) -> Path:
    """Write a portable result fixture before tests disable compilation."""
    document = _document(load_job(_copied_job(tmp_path)))
    path = tmp_path / "saved result.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_multiple_results_view_from_another_cwd_without_compiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = saved(tmp_path)
    other = tmp_path / "second.json"
    other.write_bytes(result.read_bytes())
    (tmp_path / "device.json").unlink()
    (tmp_path / "circuit.qasm3").unlink()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("View must not compile or load a job")

    monkeypatch.setattr("nadit_physical_compiler.cli.load_job", forbidden)
    monkeypatch.setattr("nadit_physical_compiler.cli.compile_job", forbidden)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    assert (
        main(
            [
                "view",
                str(result),
                str(other),
                "-o",
                "viewer.html",
                "--json",
                "trace.json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert [t["title"] for t in report["traces"]] == [result.stem, other.stem]
    html = (unrelated / "viewer.html").read_text(encoding="utf-8")
    embedded = re.search(
        r'<script id="atom-replay-data" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert embedded is not None
    assert json.loads(embedded[1]) == json.loads((unrelated / "trace.json").read_text())
    assert not re.search(r"<script[^>]*\bsrc\s*=|<link[^>]*\bhref\s*=", html)


@pytest.mark.parametrize("collision", ["result", "json"])
def test_view_rejects_overwriting_inputs_or_colliding_outputs(
    tmp_path: Path, collision: str
) -> None:
    result = saved(tmp_path)
    before = result.read_bytes()
    output = result if collision == "result" else tmp_path / "viewer.html"
    args = ["view", str(result), "-o", str(output)]
    if collision == "json":
        args += ["--json", str(output)]
    assert main(args) == 1
    assert result.read_bytes() == before


def test_view_rejects_bad_schema_without_partial_output(tmp_path: Path) -> None:
    result = tmp_path / "bad.json"
    result.write_text('{"schema":"unknown"}', encoding="utf-8")
    output = tmp_path / "viewer.html"
    assert main(["view", str(result), "-o", str(output)]) == 1
    assert not output.exists()


def test_legacy_relative_device_resolves_from_result_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = saved(tmp_path)
    document = json.loads(result.read_text())
    document["schema"] = "nadit.result/1"
    for key in ("device", "dag", "inputs"):
        document.pop(key)
    document["job"]["device"] = "device.json"
    result.write_text(json.dumps(document), encoding="utf-8")
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    assert main(["view", str(result), "-o", "legacy.html"]) == 0
    assert (unrelated / "legacy.html").is_file()
    assert not (unrelated / "traces.json").exists()
