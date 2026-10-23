"""Saved result documents supply viewer inputs without compiling again."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from examples.viewer_cases import compile_feedback
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.serialization import encode_device, encode_result
from nadit_physical_compiler.visualization import build_result_trace, build_trace


@pytest.mark.parametrize("bit", [0, 1])
def test_saved_result_matches_direct_trace(bit: int) -> None:
    example = compile_feedback(bit)
    device = encode_device(example.device)
    document = json.loads(
        json.dumps(
            encode_result(
                example.result,
                entry_state=example.entry,
                job={"device": device, "circuit": {"qasm": "unavailable.qasm"}},
                provenance={"git_commit": "fixture"},
            )
        )
    )
    trace = build_result_trace(document, device, title="Saved case")
    direct = build_trace(
        example.result.program,
        example.entry,
        example.device,
        title="Saved case",
        measurements=example.result.measurements,
        operations=example.result.operations,
    )
    metadata = trace.pop("compilation")
    assert trace == direct
    assert metadata == {
        "job": document["job"],
        "provenance": document["provenance"],
        "metrics": document["metrics"],
    }
    before = deepcopy(metadata)
    document["job"]["circuit"]["qasm"] = "changed.qasm"
    assert metadata == before


def test_saved_result_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="result schema"):
        build_result_trace({"schema": "nadit.result/2"}, {})


@pytest.mark.parametrize("device_source", ["inline", "file", "override"])
def test_viewer_cli_reads_saved_result_without_qasm_or_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, device_source: str
) -> None:
    example = compile_feedback(1)
    device = encode_device(example.device)
    device_file = tmp_path / "device.json"
    device_file.write_text(json.dumps(device), encoding="utf-8")
    recorded_device: Any = device if device_source == "inline" else str(device_file)
    if device_source == "override":
        recorded_device = "unavailable-device.json"
    document = encode_result(
        example.result,
        entry_state=example.entry,
        job={"device": recorded_device, "circuit": {"qasm": "missing.qasm"}},
        provenance={},
        validation={"ok": True, "failure": None},
    )
    result_file = tmp_path / "saved-result.json"
    result_file.write_text(json.dumps(document), encoding="utf-8")

    def unexpected_compile() -> Any:
        pytest.fail("Saved-result viewing must not compile an example.")

    monkeypatch.setattr("nadit_physical_compiler.cli.compile_job", unexpected_compile)
    destination = tmp_path / "viewer" / "index.html"
    arguments = [
        "view",
        str(result_file),
        "--output",
        str(destination),
        "--json",
        str(destination.with_name("traces.json")),
    ]
    if device_source == "override":
        arguments += ["--device", str(device_file)]
    assert main(arguments) == 0
    exported = json.loads(
        destination.with_name("traces.json").read_text(encoding="utf-8")
    )
    expected = build_result_trace(document, device, title=result_file.stem)
    assert exported == {"schema_version": 1, "traces": [expected]}
    assert expected["compilation"]["validation"] == {"ok": True, "failure": None}
    assert expected["atom_ir"] == example.result.program.to_text()
    assert destination.is_file()
