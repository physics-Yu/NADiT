"""Portable results provide viewer geometry without external Device files."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from test_result_v2 import _copied_job, _document

from examples.visualize_compilation import main
from nadit_physical_compiler.job import load_job
from nadit_physical_compiler.visualization import build_result_trace


def test_v2_viewer_uses_embedded_device_by_default(tmp_path: Path) -> None:
    document = _document(load_job(_copied_job(tmp_path)))

    embedded = build_result_trace(document)
    explicit = build_result_trace(document, document["device"])

    assert embedded == explicit
    assert embedded["compilation"]["inputs"] == document["inputs"]
    snapshot = deepcopy(embedded["compilation"])
    document["inputs"]["qasm_sha256"] = "changed"
    assert embedded["compilation"] == snapshot


def test_v1_viewer_requires_explicit_device_document(tmp_path: Path) -> None:
    document = _document(load_job(_copied_job(tmp_path)))
    document["schema"] = "nadit.result/1"
    device = document.pop("device")
    for field in ("dag", "inputs"):
        document.pop(field)

    with pytest.raises(ValueError, match="Device"):
        build_result_trace(document)
    assert build_result_trace(document, device)["steps"]


@pytest.mark.parametrize("override", [False, True])
def test_v2_viewer_cli_needs_no_original_inputs_or_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: bool
) -> None:
    document = _document(load_job(_copied_job(tmp_path)))
    result_path = tmp_path / "portable.json"
    result_path.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "device.json").unlink()
    (tmp_path / "circuit.qasm3").unlink()

    def unexpected_compile() -> Any:
        pytest.fail("Saved-result playback must not invoke compilation.")

    monkeypatch.setattr(
        "examples.visualize_compilation.compile_examples", unexpected_compile
    )
    destination = tmp_path / "viewer" / "index.html"
    arguments = ["--result", str(result_path), "--output", str(destination)]
    expected_device = deepcopy(document["device"])
    if override:
        expected_device["name"] = "Explicit alternate device"
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps(expected_device), encoding="utf-8")
        arguments += ["--device", str(override_path)]

    main(arguments)

    exported = json.loads(
        destination.with_name("traces.json").read_text(encoding="utf-8")
    )
    expected = build_result_trace(document, expected_device, title=result_path.stem)
    assert exported == {"schema_version": 1, "traces": [expected]}
    assert destination.is_file()
