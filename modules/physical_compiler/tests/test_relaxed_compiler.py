"""Relaxed transport composes with existing placement, retention, and feedback."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_ids_compiler import _dag, _strategy
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler import motion
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.device import Device, load_device
from nadit_physical_compiler.frontend import load_qasm
from nadit_physical_compiler.state import SLMHolder
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport

MODULE = Path(__file__).parents[1]
type Mode = Literal["fixed", "ids"]


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


@pytest.mark.parametrize("mode", ["fixed", "ids"])
@pytest.mark.parametrize("bit", [0, 1])
def test_relaxed_feedback_matches_staged_strict_case(
    device: Device, mode: Mode, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)
    strategy = _strategy(device, mode)
    strict_backend = RecordingBackend(bit)
    relaxed_backend = RecordingBackend(bit)

    strict = compile_physical(
        dag,
        device,
        strategy=replace(strategy, transporter=StagedStrictTransport("transport")),
        entry_state=entry,
        measurement_backend=strict_backend,
    )
    relaxed = compile_physical(
        dag,
        device,
        strategy=replace(strategy, transporter=RelaxedTransport("transport")),
        entry_state=entry,
        measurement_backend=relaxed_backend,
    )

    _audit(dag, strict, device, entry)
    _audit(dag, relaxed, device, entry)
    assert [(item.operation_id, item.status) for item in relaxed.operations] == [
        (item.operation_id, item.status) for item in strict.operations
    ]
    assert [(item.result, item.value) for item in relaxed.measurements] == [
        (item.result, item.value) for item in strict.measurements
    ]
    assert (
        strict_backend.calls
        == relaxed_backend.calls
        == [node.result for node in dag.nodes if node.result is not None]
    )
    assert relaxed.program.global_phase == strict.program.global_phase
    assert all(
        isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id.startswith("storage_")
        for atom in relaxed.exit_state.atoms
    )
    assert entry == _entry(device)


def test_relaxed_pipeline_is_deterministic_and_does_not_run_full_checkers(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Reference transport checks must stay outside construction.")

    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; c = measure q[0]; "
        "if(c) { cz q[0],q[2]; cz q[1],q[3]; } else { rz(0.2) q[1]; }"
    )
    entry = _entry(device)
    strategy = replace(_strategy(device), transporter=RelaxedTransport("transport"))
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)

    first_backend = RecordingBackend(None)
    second_backend = RecordingBackend(None)
    first = compile_physical(
        dag,
        device,
        strategy=strategy,
        entry_state=entry,
        measurement_backend=first_backend,
    )
    second = compile_physical(
        dag,
        device,
        strategy=strategy,
        entry_state=entry,
        measurement_backend=second_backend,
    )

    _audit(dag, first, device, entry)
    assert first == second
    assert first.program.to_text() == second.program.to_text()
    assert (
        first_backend.calls
        == second_backend.calls
        == [record.result for record in first.measurements]
    )
