"""Optional fixed-home reuse preserves boundaries, feedback and old behavior."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_job import MODULE
from test_qec_mode import qec_job
from test_strict_compiler import RecordingBackend

from nadit_physical_compiler.atom_ir import AtomProgram
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.job import CompilationJob, compile_job, loads_job
from nadit_physical_compiler.metrics import collect_metrics
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.schemas.job import search_smarter_schema
from nadit_physical_compiler.strategies.fixed_reuse import FixedReuseStrategy
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.validation import validate_result


def enabled(job: CompilationJob) -> CompilationJob:
    document = deepcopy(job.document)
    document["strategy"].update(cross_layer_reuse=True, reuse={"kind": "matching"})
    return loads_job(json.dumps(document), base_dir=MODULE)


@pytest.mark.parametrize("placement", ["ids", "astar", "multipair"])
@pytest.mark.parametrize("bit", [0, 1])
def test_partner_changes_feedback_replay_and_reduced_loads(
    tmp_path: Path, placement: str, bit: int
) -> None:
    off = qec_job(tmp_path, placement=placement, bit=bit)
    on = enabled(off)
    before, after = compile_job(off), compile_job(on)
    assert isinstance(on.strategy, FixedReuseStrategy)
    assert before.measurements == after.measurements or [
        (m.result, m.value) for m in before.measurements
    ] == [(m.result, m.value) for m in after.measurements]
    assert validate_result(
        after, entry_state=on.entry_state, device=on.device, dag=on.dag
    )["ok"]
    assert (
        replay(after.program, on.entry_state, on.device)
        == on.entry_state
        == after.exit_state
    )
    assert (
        collect_metrics(after)["atom_load_count"]
        < collect_metrics(before)["atom_load_count"]
    )
    assert on.strategy.home_layout is None
    assert compile_job(on).program == after.program


def test_switch_off_preserves_original_and_schema_exposes_switch(
    tmp_path: Path,
) -> None:
    off = qec_job(tmp_path)
    doc = deepcopy(off.document)
    doc["strategy"]["cross_layer_reuse"] = False
    explicit = loads_job(json.dumps(doc), base_dir=MODULE)
    assert compile_job(off).program == compile_job(explicit).program
    assert (
        search_smarter_schema()["properties"]["cross_layer_reuse"]["default"] is False
    )
    assert (
        "cross_layer_reuse"
        in describe()["compilers"]["search_smarter"]["options"]["properties"]
    )


def test_binding_does_not_leak_homes_to_another_compilation(tmp_path: Path) -> None:
    job = enabled(qec_job(tmp_path))
    assert isinstance(job.strategy, FixedReuseStrategy)
    a, b, *rest = job.entry_state.atoms
    changed = replace(
        job.entry_state,
        atoms=(
            replace(a, position=b.position, holder=b.holder),
            replace(b, position=a.position, holder=a.holder),
            *rest,
        ),
    )
    first = compile_job(job)
    second = compile_physical(
        job.dag,
        job.device,
        strategy=job.strategy,
        entry_state=changed,
        measurement_backend=job.measurement_backend,
    )
    assert first.exit_state == job.entry_state and second.exit_state == changed
    assert validate_result(second, entry_state=changed, device=job.device, dag=job.dag)[
        "ok"
    ]


@pytest.mark.parametrize("value", ["true", 1])
def test_nonboolean_switch_is_rejected(tmp_path: Path, value: Any) -> None:
    doc = qec_job(tmp_path).document
    doc["strategy"]["cross_layer_reuse"] = value
    with pytest.raises(ValueError, match="cross_layer_reuse"):
        loads_job(json.dumps(doc), base_dir=MODULE)


@pytest.mark.parametrize("transport", ["strict", "staged", "relaxed"])
def test_repeated_pair_saves_two_loads_per_retained_atom(
    tmp_path: Path, transport: str
) -> None:
    base = qec_job(tmp_path, transport=transport)
    doc = deepcopy(base.document)
    path = tmp_path / "repeated.qasm3"
    path.write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; '
        "cz q[0],q[1]; barrier q; rx(0.2) q[0]; cz q[0],q[1];"
    )
    doc["circuit"]["qasm"] = str(path)
    doc["case"]["overrides"] = {}
    off = loads_job(json.dumps(doc), base_dir=MODULE)
    on = enabled(off)
    a, b = compile_job(off), compile_job(on)
    assert (
        collect_metrics(a)["atom_load_count"] - collect_metrics(b)["atom_load_count"]
        == 4
    )
    assert validate_result(b, entry_state=on.entry_state, device=on.device, dag=on.dag)[
        "ok"
    ]
    assert b.exit_state == on.entry_state


@pytest.mark.parametrize("bit", [0, 1])
def test_window_boundaries_spectators_and_single_sampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bit: int
) -> None:
    doc = qec_job(tmp_path).document
    path = tmp_path / "windows.qasm3"
    path.write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; bit m; '
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "rx(0.2) q[0]; cz q[0],q[2]; barrier q; cz q[1],q[3]; "
        "m=measure q[0]; cz q[1],q[2]; "
        "if(m) {rz(0.2) q[1];} cz q[1],q[2]; reset q[1];"
    )
    doc["circuit"]["qasm"] = str(path)
    doc["case"]["overrides"] = {}
    job = enabled(loads_job(json.dumps(doc), base_dir=MODULE))
    assert isinstance(job.strategy, FixedReuseStrategy)
    backend = RecordingBackend(bit)
    lower = FixedReuseStrategy.lower
    seen: list[tuple[str, int, int]] = []

    def audited(self: FixedReuseStrategy, *args: Any, **kwargs: Any) -> Any:
        dag, layer, layout, device = args
        fragment = lower(self, *args, **kwargs)
        name = dag.nodes[layer.operation_ids[0]].name
        seen.append((name, len(backend.calls), len(fragment.retained_atoms)))
        assert (
            replay(AtomProgram(fragment.steps), layout, device) == fragment.exit_layout
        )
        retained = {a.id for a in fragment.retained_atoms}
        for atom in fragment.exit_layout.atoms:
            if atom.id not in retained:
                assert atom == job.entry_state.atom(atom.id)
        if name in ("measure", "reset"):
            assert fragment.exit_layout == job.entry_state and not retained
        return fragment

    monkeypatch.setattr(FixedReuseStrategy, "lower", audited)
    result = compile_physical(
        job.dag,
        job.device,
        strategy=job.strategy,
        entry_state=job.entry_state,
        measurement_backend=backend,
    )
    assert len(backend.calls) == len(set(backend.calls)) == 1
    assert seen[0][1] == 0
    assert any(
        name == "cz" and calls == 1 and retained == 2 for name, calls, retained in seen
    )
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
    assert result.exit_state == job.entry_state


def test_supported_rigid_transport_and_source_reuse(tmp_path: Path) -> None:
    job = enabled(qec_job(tmp_path, rigid=True))
    doc = job.document
    doc["strategy"]["reuse"] = {"kind": "source"}
    job = loads_job(json.dumps(doc), base_dir=MODULE)
    result = compile_job(job)
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
    assert result.exit_state == job.entry_state


def test_wrong_mode_and_missing_analyzer_are_rejected(tmp_path: Path) -> None:
    doc = qec_job(tmp_path).document
    doc["strategy"]["cross_layer_reuse"] = True
    with pytest.raises(ValueError, match="reuse.kind"):
        loads_job(json.dumps(doc), base_dir=MODULE)
    doc["strategy"]["mode"] = "adaptive"
    with pytest.raises(ValueError, match="mode=qec"):
        loads_job(json.dumps(doc), base_dir=MODULE)


def test_pair_placement_has_explicit_unsupported_result(tmp_path: Path) -> None:
    doc = qec_job(tmp_path).document
    doc["strategy"].update(
        cross_layer_reuse=True, reuse={"kind": "matching"}, placement={"kind": "pair"}
    )
    with pytest.raises(UnsupportedJob, match="catalogue execution placer"):
        loads_job(json.dumps(doc), base_dir=MODULE)
