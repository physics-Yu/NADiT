"""Fixed-home shared-array compilation and independent action replay."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler import FakeBackend, loads_qasm
from nadit_physical_compiler.atom_ir import AtomProgram, CZPulse, Load, Measure
from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import (
    AOD,
    Device,
    PairArray,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
)
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.physical_ir import PhysicalDAG, ResultRef
from nadit_physical_compiler.placement import GatePairCatalog
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.schemas.job import job_schema
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.strategies.nonzoned import NonZonedStrategy
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.validation import validate_result


def shared_device(rows: int = 1, columns: int = 4) -> tuple[Device, AtomLayout]:
    left = tuple(
        tuple(f"l{r * columns + c}" for c in range(columns)) for r in range(rows)
    )
    right = tuple(
        tuple(f"r{r * columns + c}" for c in range(columns)) for r in range(rows)
    )
    sites = tuple(
        SLMSite(f"{side}{r * columns + c}", Position(10 * c + offset, 10 * r))
        for r in range(rows)
        for c in range(columns)
        for side, offset in (("l", 0), ("r", 2))
    )
    bounds = Region(-2, -2, columns * 10 - 5, rows * 10 - 5)
    device = Device(
        "shared",
        (
            Zone("shared", bounds, ("cz", "rotate", "reset")),
            Zone(
                "readout",
                Region(-2, rows * 10 + 10, columns * 10, rows * 10 + 20),
                ("measure",),
            ),
        ),
        sites,
        (AOD("a", Region(-5, -5, columns * 10 + 5, rows * 10 + 25), 1),),
        Timing(10, 10, 1),
        cz=CZCapability("rydberg", 4.6, 0.5, allow_spectators=True),
        local=LocalControl("local", 1, 5),
        readout=ReadoutCapability("camera", 10, 500, 5, True, ("aod",), None),
        pair_arrays=(PairArray("cells", "shared", left, right),),
    )
    entry = AtomLayout(
        tuple(
            AtomState(q, device.site(f"l{q}").position, SLMHolder(f"l{q}"))
            for q in range(rows * columns)
        ),
        qubit_to_atom=tuple(range(rows * columns)),
    )
    return device, entry


def circuit(n: int, body: str) -> PhysicalDAG:
    return loads_qasm(f'OPENQASM 3.0; include "stdgates.inc"; qubit[{n}] q; {body}')


def strategy(device: Device) -> NonZonedStrategy:
    return NonZonedStrategy("a", GatePairCatalog.from_pair_arrays(device))


@pytest.mark.parametrize(
    "body,batches",
    [
        ("cz q[0], q[2]; cz q[1], q[3];", 1),
        ("cz q[0], q[3]; cz q[1], q[2];", 2),
    ],
)
def test_one_layer_has_one_pulse_after_all_arrivals(body: str, batches: int) -> None:
    device, entry = shared_device()
    dag = circuit(4, body)
    result = compile_physical(dag, device, strategy=strategy(device), entry_state=entry)
    pulse_indices = [
        i
        for i, step in enumerate(result.program.steps)
        if isinstance(step.actions[0], CZPulse)
    ]
    assert len(pulse_indices) == 1
    pulse_index = pulse_indices[0]
    forward = result.program.steps[:pulse_index]
    assert sum(isinstance(step.actions[0], Load) for step in forward) == batches
    assert len(result.program.steps[pulse_index].actions[0].source_ids) == 2
    assert result.exit_state == replay(result.program, entry, device) == entry
    report = validate_result(result, entry_state=entry, device=device, dag=dag)
    assert report["ok"], report


@pytest.mark.parametrize("max_rows,expected", [(None, 1), (1, 2)])
def test_pickup_closure_and_line_capacity(max_rows: int | None, expected: int) -> None:
    device, entry = shared_device(2)
    device = replace(device, aods=(replace(device.aods[0], max_rows=max_rows),))
    dag = circuit(8, "cz q[0],q[2]; cz q[5],q[7]; cz q[1],q[3]; cz q[4],q[6];")
    result = compile_physical(dag, device, strategy=strategy(device), entry_state=entry)
    before = []
    for step in result.program.steps:
        if isinstance(step.actions[0], CZPulse):
            break
        before.append(step)
    loads = [s.actions[0] for s in before if isinstance(s.actions[0], Load)]
    assert len(loads) == expected
    assert sum(len(a.bindings) for a in loads) == 4
    assert replay(result.program, entry, device) == entry
    report = validate_result(result, entry_state=entry, device=device, dag=dag)
    assert report["ok"], report


def test_repeated_pairs_and_partner_changes_restore_each_layer() -> None:
    device, entry = shared_device()
    dag = circuit(
        4,
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; cz q[0],q[2]; cz q[1],q[3];",
    )
    selected = strategy(device)
    result = compile_physical(dag, device, strategy=selected, entry_state=entry)
    assert len(result.layers) == 3
    for layer in result.layers:
        fragment = selected.lower(dag, layer, entry, device)
        assert fragment.exit_layout == entry
        assert replay(AtomProgram(fragment.steps), entry, device) == entry
    assert (
        len(
            [
                a
                for s in result.program.steps
                for a in s.actions
                if isinstance(a, CZPulse)
            ]
        )
        == 3
    )
    assert result.exit_state == entry


@pytest.mark.parametrize("bit", [0, 1])
def test_feedback_measurement_identity_and_delay(bit: int) -> None:
    device, entry = shared_device()
    dag = circuit(
        4,
        "bit c; cz q[0],q[1]; c = measure q[0]; reset q[0]; "
        "if (c == true) { cz q[0],q[2]; } c = measure q[0];",
    )
    refs = [n.result for n in dag.nodes if n.result is not None]
    backend = FakeBackend(overrides={ref: bit for ref in refs})
    result = compile_physical(
        dag,
        device,
        strategy=strategy(device),
        entry_state=entry,
        measurement_backend=backend,
    )
    assert len(result.measurements) == 2
    assert {m.result for m in result.measurements} == set(refs)
    by_id = {o.operation_id: o for o in result.operations}
    records = {m.result: m for m in result.measurements}
    for node in dag.nodes:
        if node.condition:
            assert by_id[node.id].status == ("emitted" if bit else "skipped")
            if bit:
                assert (
                    by_id[node.id].start_us
                    >= records[node.condition.result].available_at_us
                )
    assert (
        sum(
            len(a.results)
            for s in result.program.steps
            for a in s.actions
            if isinstance(a, Measure)
        )
        == 2
    )
    assert result.exit_state == replay(result.program, entry, device) == entry
    report = validate_result(result, entry_state=entry, device=device, dag=dag)
    assert report["ok"], report


def test_allow_spectators_does_not_allow_extra_couplings() -> None:
    device, entry = shared_device()
    dag = circuit(4, "cz q[0],q[1];")
    result = compile_physical(dag, device, strategy=strategy(device), entry_state=entry)
    assert device.cz is not None
    strict = replace(device, cz=replace(device.cz, allow_spectators=False))
    assert not validate_result(result, entry_state=entry, device=strict, dag=dag)["ok"]
    # A larger physical interaction radius creates unwanted couplings despite
    # permitting isolated illuminated spectators.
    coupled = replace(device, cz=replace(device.cz, radius_um=50))
    assert not validate_result(result, entry_state=entry, device=coupled, dag=dag)["ok"]


def job_document(tmp_path: Path) -> dict[str, Any]:
    device, entry = shared_device()
    (tmp_path / "c.qasm").write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; cz q[0],q[2]; cz q[1],q[3];',
        encoding="utf-8",
    )
    return {
        "schema": "nadit.job/1",
        "circuit": {"qasm": "c.qasm"},
        "device": encode_device(device),
        "aod": "a",
        "strategy": {"kind": "nonzoned"},
        "entry": {
            "kind": "explicit",
            "atoms": [[a.id, f"l{a.id}", a.id] for a in entry.atoms],
        },
    }


def test_job_selection_roundtrip_and_discovery(tmp_path: Path) -> None:
    document = job_document(tmp_path)
    job = loads_job(json.dumps(document), base_dir=tmp_path)
    assert isinstance(job.strategy, NonZonedStrategy)
    result = compile_job(job)
    assert result.exit_state == job.entry_state
    reloaded = loads_job(json.dumps(job.document), base_dir=tmp_path)
    assert compile_job(reloaded).program == result.program
    assert "nonzoned" in describe()["compiler_strategies"]
    assert any(
        s.get("properties", {}).get("kind", {}).get("const") == "nonzoned"
        for s in job_schema()["properties"]["strategy"]["oneOf"]
    )


@pytest.mark.parametrize(
    "change", ["rigid", "no_pairs", "no_spectators", "auto_entry", "aod_cz_only"]
)
def test_unsupported_job_is_admitted_before_compilation(
    tmp_path: Path, change: str
) -> None:
    document = job_document(tmp_path)
    device = document["device"]
    if change == "rigid":
        device["aods"][0]["motion"] = "rigid"
    elif change == "no_pairs":
        device["pair_arrays"] = []
    elif change == "no_spectators":
        device["cz"]["allow_spectators"] = False
    elif change == "aod_cz_only":
        device["cz"]["holder_pairs"] = [["aod", "aod"]]
    else:
        document["entry"] = {"kind": "initial_storage"}
    with pytest.raises(UnsupportedJob):
        loads_job(json.dumps(document), base_dir=tmp_path)


def test_invalid_home_site_does_not_silently_relocate() -> None:
    device, entry = shared_device()
    extra = SLMSite("other", Position(0, 25))
    device = replace(device, slm_sites=(*device.slm_sites, extra))
    entry = replace(
        entry,
        atoms=(AtomState(0, extra.position, SLMHolder(extra.id)), *entry.atoms[1:]),
    )
    dag = circuit(4, "cz q[0],q[1];")
    with pytest.raises(ValueError, match="reference"):
        strategy(device).lower(dag, GateLayer((0,)), entry, device)


def test_initial_pair_already_together_needs_no_transport() -> None:
    device, entry = shared_device()
    site = device.site("r0")
    entry = replace(
        entry,
        atoms=(
            entry.atoms[0],
            AtomState(1, site.position, SLMHolder(site.id)),
            *entry.atoms[2:],
        ),
    )
    dag = circuit(4, "cz q[0],q[1];")
    result = compile_physical(dag, device, strategy=strategy(device), entry_state=entry)
    assert len(result.program.steps) == 1
    assert isinstance(result.program.steps[0].actions[0], CZPulse)
    assert validate_result(result, entry_state=entry, device=device, dag=dag)["ok"]


def test_layer_limit_and_each_measurement_sampled_once() -> None:
    device, entry = shared_device()
    dag = circuit(
        4,
        "bit[2] m; cz q[0],q[2]; cz q[1],q[3]; barrier q; "
        "m[0] = measure q[0]; m[1] = measure q[1]; "
        "if (m[0] == true) { rx(0.1) q[0]; rz(0.2) q[1]; }",
    )
    calls: list[ResultRef] = []

    class Backend(FakeBackend):
        def measure(self, result: ResultRef) -> int:
            calls.append(result)
            return 1

    selected = replace(strategy(device), max_cz_gates=1)
    result = compile_physical(
        dag, device, strategy=selected, entry_state=entry, measurement_backend=Backend()
    )
    assert len(calls) == len(set(calls)) == 2
    assert (
        sum(isinstance(a, CZPulse) for s in result.program.steps for a in s.actions)
        == 2
    )
    assert result.exit_state == entry
    assert validate_result(result, entry_state=entry, device=device, dag=dag)["ok"]


def test_cli_saved_result_and_unsupported_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = job_document(tmp_path)
    path, result = tmp_path / "job.json", tmp_path / "result.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["compile", str(path), "--validate", "-o", str(result)]) == 0
    capsys.readouterr()
    assert main(["validate", str(result)]) == 0
    saved = json.loads(result.read_text(encoding="utf-8"))
    assert saved["schema"] == "nadit.result/2"
    assert saved["device"]["cz"]["allow_spectators"] is True
    capsys.readouterr()
    document["device"]["aods"][0]["motion"] = "rigid"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["compile", str(path)]) == 4


def test_column_capacity_preserves_closed_multirow_groups() -> None:
    device, entry = shared_device(2)
    device = replace(device, aods=(replace(device.aods[0], max_columns=1),))
    dag = circuit(8, "cz q[0],q[2]; cz q[5],q[7]; cz q[1],q[3]; cz q[4],q[6];")
    result = compile_physical(dag, device, strategy=strategy(device), entry_state=entry)
    loads = [a for s in result.program.steps for a in s.actions if isinstance(a, Load)]
    assert len(loads) == 4  # Two arrivals and their two inverses.
    assert all(len(a.grid.columns) == 1 for a in loads)
    assert result.exit_state == entry
    assert validate_result(result, entry_state=entry, device=device, dag=dag)["ok"]


def test_spectator_policy_is_explicit_and_boolean() -> None:
    assert not CZCapability("cz", 4.6, 1).allow_spectators
    with pytest.raises(ValueError, match="boolean"):
        CZCapability("cz", 4.6, 1, allow_spectators="false")  # type: ignore[arg-type]
