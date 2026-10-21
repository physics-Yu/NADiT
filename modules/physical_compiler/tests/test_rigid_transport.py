"""Rigid transport admission, feedback compilation, and independent replay."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_strict_transport import _case, _loads
from test_strict_transport import _verify as verify_strict
from test_transport import _device, _readout, _stored, _verify

from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.device import Device, Position
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.transport import (
    PairTransport,
    RelaxedTransport,
    StagedStrictTransport,
    StrictTransport,
)
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]


def _rigid(device: Device) -> Device:
    return replace(
        device, aods=tuple(replace(aod, motion="rigid") for aod in device.aods)
    )


def test_pair_equal_displacement_moves_together() -> None:
    device = _rigid(_device())
    device = replace(
        device,
        slm_sites=tuple(
            replace(site, position=Position(15, 20)) if site.id == "g1" else site
            for site in device.slm_sites
        ),
    )
    entry = _stored(device, "s0", "s1")
    target = _stored(device, "g0", "g1")

    fragment = PairTransport("aod").construct(entry, target, device)

    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Move, Store]
    assert _loads(fragment) == ((2, 7),)
    _verify(fragment, entry, target, device)


def test_pair_unequal_displacement_uses_singletons() -> None:
    device = _rigid(_device())
    entry = _stored(device, "s0", "s1")
    target = _stored(device, "g0", "g1")

    fragment = PairTransport("aod").construct(entry, target, device)

    assert _loads(fragment) == ((2,), (7,))
    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Move, Store] * 2
    _verify(fragment, entry, target, device)


@pytest.mark.parametrize(
    "rows,columns,batch_sizes", [(None, None, [4]), (1, 2, [2, 2]), (2, 1, [2, 2])]
)
def test_strict_common_translation_respects_line_capacity(
    rows: int | None, columns: int | None, batch_sizes: list[int]
) -> None:
    sources = ((0, 0), (5, 0), (0, 5), (5, 5))
    targets = tuple((x + 10, y + 20) for x, y in sources)
    device, entry, target = _case(sources, targets, rows=rows, columns=columns)
    device = _rigid(device)

    fragment = StrictTransport("a").construct(entry, target, device)

    assert [len(batch) for batch in _loads(fragment)] == batch_sizes
    verify_strict(fragment, entry, target, device)


def test_strict_splits_unequal_displacements() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 0), (10, 0)), ((10, 20), (15, 20), (22, 20))
    )
    device = _rigid(device)

    fragment = StrictTransport("a").construct(entry, target, device)

    assert _loads(fragment) == ((0, 1), (2,))
    verify_strict(fragment, entry, target, device)


def test_strict_ghost_cannot_join_a_different_translation() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5), (0, 5)), ((10, 20), (15, 25), (12, 25))
    )
    device = _rigid(device)

    fragment = StrictTransport("a").construct(entry, target, device)

    assert _loads(fragment) == ((0,), (1,), (2,))
    verify_strict(fragment, entry, target, device)


@pytest.mark.parametrize("kind", ["staged", "relaxed"])
@pytest.mark.parametrize("transition", ["noop", "transfer", "readout", "carried_noop"])
def test_staged_and_relaxed_reject_rigid_before_fallbacks(
    kind: str, transition: str
) -> None:
    device = _rigid(_device())
    entry = _stored(device, "s0", "s1")
    target = entry
    if transition == "transfer":
        target = _stored(device, "g0", "g1")
    elif transition == "readout":
        target = _readout(entry, Position(20, 40))
    elif transition == "carried_noop":
        entry = target = _readout(entry, Position(20, 40))
    transporter = (
        StagedStrictTransport("aod") if kind == "staged" else RelaxedTransport("aod")
    )

    with pytest.raises(ValueError, match="rigid"):
        transporter.construct(entry, target, device)


@pytest.mark.parametrize("kind", ["staged", "relaxed"])
def test_job_rejects_unsupported_rigid_transport_and_cli_reports_input_error(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = load_job(MODULE / "examples/jobs/multipair.json")
    document = base.document
    document["device"] = encode_device(_rigid(base.device))
    document["strategy"]["transport"] = {"kind": kind}

    with pytest.raises(ValueError, match=r"strategy\.transport.*rigid"):
        loads_job(json.dumps(document), base_dir=MODULE)

    path = tmp_path / "job.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["compile", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "strategy.transport" in output.err and "rigid" in output.err


@pytest.mark.parametrize("transport", ["pair", "sequential", "strict"])
@pytest.mark.parametrize("bit", [0, 1])
def test_rigid_feedback_compile_validates_and_replays(transport: str, bit: int) -> None:
    case = "pair" if transport == "pair" else "multipair"
    base = load_job(MODULE / f"examples/jobs/{case}.json")
    document = base.document
    document["device"] = encode_device(_rigid(base.device))
    document["strategy"]["transport"] = {"kind": transport}
    if transport != "pair":
        document["strategy"]["placement"] = {"kind": "ids"}
        document["strategy"]["reuse"] = {"kind": "matching"}
    document["case"]["overrides"] = {
        str(node.result.measurement_id): bit
        for node in base.dag.nodes
        if node.result is not None
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)

    result = compile_job(job)

    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"], report
    assert report["semantic"] == "checked"
    assert result.measurements and all(
        record.value == bit for record in result.measurements
    )
    assert len(result.measurements) == len(document["case"]["overrides"])
    records = {record.operation_id: record for record in result.operations}
    for node in job.dag.nodes:
        if node.condition is not None:
            skipped = node.condition.value != bit
            assert (records[node.id].status == "skipped") == skipped
            if not skipped:
                measurement = next(
                    item
                    for item in result.measurements
                    if item.result == node.condition.result
                )
                assert records[node.id].start_us >= measurement.available_at_us
    current = job.entry_state
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, Load):
                checked = check_load(
                    job.device,
                    current,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
            elif isinstance(action, Move):
                checked = check_move(
                    job.device, current, action.grid, dict(action.targets)
                )
            elif isinstance(action, Store):
                checked = check_store(
                    job.device, current, action.grid, dict(action.bindings)
                )
            else:
                continue
            assert checked.ok, checked
        current = apply_step(current, step, job.device)
    assert current == result.exit_state
    assert replay(result.program, job.entry_state, job.device) == result.exit_state
