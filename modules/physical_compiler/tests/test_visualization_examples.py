"""Viewer samples are deterministic products of the physical compiler."""

import json
from pathlib import Path

import pytest

from examples.viewer_cases import compile_feedback, compile_static
from examples.visualize_compilation import example_trace, main
from nadit_physical_compiler import CZPulse, Measure, Move, replay
from nadit_physical_compiler.visualization import build_result_trace


@pytest.mark.parametrize("bit", [0, 1])
def test_feedback_example_replays_and_waits_for_compiled_result(bit: int) -> None:
    example = compile_feedback(bit)
    result = example.result
    assert replay(result.program, example.entry, example.device) == result.exit_state
    assert [record.value for record in result.measurements] == [bit]
    availability = result.measurements[0].available_at_us
    operations = {operation.operation_id: operation for operation in result.operations}
    for node in example.dag.nodes:
        if node.condition is not None:
            compiled = operations[node.id]
            if node.name == "global_phase" and node.condition.value == bit:
                assert compiled.status == "metadata"
            else:
                assert compiled.status == (
                    "emitted" if node.condition.value == bit else "skipped"
                )
            assert compiled.start_us >= availability
    elapsed = 0.0
    measure_end = None
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, Measure):
                measure_end = elapsed + action.duration_us
        elapsed += step.duration_us
    assert measure_end is not None
    assert example.device.readout is not None
    assert availability == measure_end + example.device.readout.latency_us
    assert compile_feedback(bit).result == result


def test_static_example_compiles_sixty_atoms_with_parallel_movement() -> None:
    example = compile_static()
    result = example.result
    assert example.dag.num_qubits == len(example.entry.atoms) == 60
    assert sum(node.name == "cz" for node in example.dag.nodes) == 90
    assert replay(result.program, example.entry, example.device) == result.exit_state
    actions = [action for step in result.program.steps for action in step.actions]
    assert (
        max(len(action.targets) for action in actions if isinstance(action, Move)) > 1
    )
    assert (
        max(len(action.source_ids) for action in actions if isinstance(action, CZPulse))
        > 1
    )
    assert all(operation.status != "skipped" for operation in result.operations)
    assert not result.measurements
    assert "local demonstration" in example.description


def test_feedback_example_rejects_non_bit_override() -> None:
    with pytest.raises(ValueError, match="measurement bit 0 or 1"):
        compile_feedback(2)


def test_export_keeps_compiled_measurement_and_source_metadata() -> None:
    example = compile_feedback(1)
    trace = example_trace(example)
    record = example.result.measurements[0]
    assert trace["measurements"] == [
        {
            "measurement_id": record.result.measurement_id,
            "value": record.value,
            "available_at_us": record.available_at_us,
        }
    ]
    assert trace["source_qasm"] == example.source_qasm
    assert trace["compilation"] == example.config
    assert trace["atom_ir"] == example.result.program.to_text()


def test_cli_writes_actual_compilation_as_html_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = compile_feedback(0)
    monkeypatch.setattr(
        "examples.visualize_compilation.compile_examples", lambda: (example,)
    )
    destination = tmp_path / "viewer" / "index.html"
    main(["--output", str(destination)])
    data = json.loads(destination.with_name("traces.json").read_text(encoding="utf-8"))
    assert data == {
        "schema_version": 1,
        "traces": [build_result_trace(example.document, title=example.title)],
    }
    html = destination.read_text(encoding="utf-8")
    assert 'id="atom-replay-data"' in html
    assert example.title in html
