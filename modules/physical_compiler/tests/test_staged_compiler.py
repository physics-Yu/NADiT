"""Staged strict transport preserves compiler decisions and atom-action legality."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_ids_compiler import _dag, _strategy
from test_retention_regression import _audit_rotations_and_pulses
from test_strict_compiler import RecordingBackend, _audit, _entry

from nadit_physical_compiler import motion
from nadit_physical_compiler.atom_ir import CZPulse, Load, Move, Store
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.device import Device, load_device
from nadit_physical_compiler.frontend import load_qasm
from nadit_physical_compiler.state import SLMHolder
from nadit_physical_compiler.transport import StagedStrictTransport

MODULE = Path(__file__).parents[1]
type Mode = Literal["fixed", "ids"]


@pytest.fixture
def device() -> Device:
    return load_device(MODULE / "configs/multipair_flexible.json")


@pytest.mark.parametrize("mode", ["fixed", "ids"])
@pytest.mark.parametrize("bit", [0, 1])
def test_staged_feedback_matches_direct_strict_case(
    device: Device, mode: Mode, bit: int
) -> None:
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    entry = _entry(device)
    direct_strategy = _strategy(device, mode)
    staged_strategy = replace(
        direct_strategy, transporter=StagedStrictTransport("transport")
    )
    direct_backend = RecordingBackend(bit)
    staged_backend = RecordingBackend(bit)

    direct = compile_physical(
        dag,
        device,
        strategy=direct_strategy,
        entry_state=entry,
        measurement_backend=direct_backend,
    )
    staged = compile_physical(
        dag,
        device,
        strategy=staged_strategy,
        entry_state=entry,
        measurement_backend=staged_backend,
    )

    _audit(dag, direct, device, entry)
    _audit(dag, staged, device, entry)
    assert [(item.operation_id, item.status) for item in staged.operations] == [
        (item.operation_id, item.status) for item in direct.operations
    ]
    assert [(item.result, item.value) for item in staged.measurements] == [
        (item.result, item.value) for item in direct.measurements
    ]
    assert (
        staged_backend.calls
        == direct_backend.calls
        == [node.result for node in dag.nodes if node.result is not None]
    )
    assert staged.program.global_phase == direct.program.global_phase
    assert all(
        isinstance(atom.holder, SLMHolder)
        and atom.holder.site_id.startswith("storage_")
        for atom in staged.exit_state.atoms
    )
    assert entry == _entry(device)


@pytest.mark.parametrize("capacity,columns", [(1, 1), (2, 2)])
def test_staged_matching_respects_gate_and_aod_capacities(
    device: Device, capacity: int, columns: int
) -> None:
    device = replace(
        device,
        aods=(replace(device.aod("transport"), max_rows=1, max_columns=columns),),
    )
    dag = _dag(
        "cz q[0],q[1]; cz q[2],q[3]; cz q[4],q[5]; barrier q; "
        "cz q[0],q[2]; cz q[1],q[4]; cz q[3],q[5];",
        6,
    )
    entry = _entry(device, 6)
    strategy = replace(
        _strategy(device, "ids", capacity),
        transporter=StagedStrictTransport("transport"),
    )

    result = compile_physical(dag, device, strategy=strategy, entry_state=entry)

    _audit(dag, result, device, entry)
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, CZPulse):
                assert len(action.source_ids) <= capacity
            elif isinstance(action, Load | Move | Store):
                assert len(action.grid.rows) <= 1
                assert len(action.grid.columns) <= columns
    assert result.measurements == ()


@pytest.mark.parametrize("mode", ["fixed", "ids"])
def test_staged_preparation_handles_all_gate_sites_occupied_by_local_controls(
    device: Device, mode: Mode
) -> None:
    device = replace(
        device,
        zones=tuple(
            replace(
                zone,
                operations=tuple(name for name in zone.operations if name != "rotate"),
            )
            if zone.id == "storage"
            else zone
            for zone in device.zones
        ),
    )
    dag = _dag("rx(0.3) q; cz q[0],q[2]; cz q[1],q[3];")
    entry = _entry(device)
    strategy = replace(
        _strategy(device, mode),
        transporter=StagedStrictTransport("transport"),
    )

    result = compile_physical(dag, device, strategy=strategy, entry_state=entry)

    rotations = _audit_rotations_and_pulses(dag, result, device, entry)
    assert len(rotations) == 4
    assert all(
        isinstance(atom.holder, SLMHolder) and atom.holder.site_id.startswith("gate_")
        for atom in rotations[0].atoms
    )


def test_staged_construction_does_not_call_full_transport_checks(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Full transport checks must stay outside construction.")

    dag = _dag("cz q[0],q[1]; cz q[2],q[3]; cz q[0],q[2]; cz q[1],q[3];")
    entry = _entry(device)
    strategy = replace(
        _strategy(device), transporter=StagedStrictTransport("transport")
    )
    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)

    result = compile_physical(dag, device, strategy=strategy, entry_state=entry)

    _audit(dag, result, device, entry)
