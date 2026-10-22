"""Optional initial placement does not change fixed-home execution semantics."""

import ast
import json
import math
import random
import sys
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from test_nonzoned import circuit, job_document, shared_device

from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.placement.enola_initial import (
    initial_cz_layers,
    make_enola_initial_layout,
)
from nadit_physical_compiler.placement.enola_sa import place_initial
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.schemas.job import job_schema
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.validation import validate_result


def test_source_placer_parity_under_identical_budget() -> None:
    raw = (
        files("nadit_physical_compiler.placement")
        .joinpath("enola_source/placer.py.txt")
        .read_bytes()
    )
    nodes = [
        n
        for n in ast.parse(raw).body
        if isinstance(n, ast.ClassDef) and n.name == "SAPlacer"
    ]
    rng = random.Random(3)
    scope: dict[str, Any] = {
        "math": math,
        "sys": sys,
        "randrange": rng.randrange,
        "shuffle": rng.shuffle,
        "uniform": rng.uniform,
        "print": lambda *args: None,
    }
    exec(compile(ast.Module(body=[*nodes], type_ignores=[]), "upstream", "exec"), scope)
    original = scope["SAPlacer"]
    initialize = original.initialize_param

    def bounded(self: Any) -> None:
        initialize(self)
        self.sa_iter_limit = 7
        self.sa_l = 40

    original.initialize_param = bounded
    layers = (((0, 1), (2, 3)), ((1, 2),), ((0, 3),))
    placer = original()
    placer.run((3, 2), 4, layers)
    expected = tuple(placer.best_mapping)
    assert (
        place_initial((3, 2), 4, layers, seed=3, iterations=8, moves_per_iteration=40)
        == expected
    )


def test_rng_is_local_and_initial_layout_is_repeatable() -> None:
    device, _ = shared_device(2)
    dag = circuit(8, "cz q[0],q[7]; cz q[1],q[6]; cz q[2],q[5]; cz q[3],q[4];")
    state = random.getstate()
    first = make_enola_initial_layout(
        dag, device, seed=8, iterations=4, moves_per_iteration=20
    )
    second = make_enola_initial_layout(
        dag, device, seed=8, iterations=4, moves_per_iteration=20
    )
    assert first == second
    assert random.getstate() == state
    assert len({a.holder.site_id for a in first.atoms}) == 8  # type: ignore[union-attr]
    assert first.qubit_to_atom == tuple(range(8))


@pytest.mark.parametrize(
    "n,body", [(1, "rx(0.2) q[0];"), (2, "cz q[0],q[1];"), (2, "")]
)
def test_flat_and_no_gate_inputs(n: int, body: str) -> None:
    device, _ = shared_device(1, n)
    dag = circuit(n, body)
    layout = make_enola_initial_layout(dag, device, iterations=2, moves_per_iteration=8)
    assert len(layout.atoms) == n
    assert {a.position for a in layout.atoms} == {
        device.site(f"l{i}").position for i in range(n)
    }


def test_feedback_prefix_keeps_dag_and_stops_before_measurement() -> None:
    dag = circuit(
        4,
        "bit c; reset q[0]; cz q[0],q[1]; cz q[2],q[3]; "
        "barrier q; rx(0.2) q[1]; cz q[1],q[2]; c = measure q[0]; "
        "if (c == true) { cz q[0],q[3]; } cz q[0],q[2];",
    )
    before = dag.nodes, dag.edges
    assert initial_cz_layers(dag) == (((0, 1), (2, 3)), ((1, 2),))
    assert (dag.nodes, dag.edges) == before


def test_measurement_boundary_only_blocks_its_dependent_region() -> None:
    dag = circuit(
        4,
        "bit c; c = measure q[0]; cz q[2],q[3]; "
        "if (c == true) { cz q[0],q[1]; } cz q[0],q[2];",
    )
    assert initial_cz_layers(dag) == (((2, 3),),)


@pytest.mark.parametrize("bit", [0, 1])
def test_generated_entry_job_feedback_and_exact_return(
    tmp_path: Path, bit: int
) -> None:
    document = job_document(tmp_path)
    (tmp_path / "c.qasm").write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; bit c; '
        "cz q[0],q[2]; cz q[1],q[3]; c = measure q[0]; "
        "if (c == true) { cz q[0],q[3]; }",
        encoding="utf-8",
    )
    document["entry"] = {
        "kind": "enola_initial",
        "seed": 2,
        "iterations": 4,
        "moves_per_iteration": 20,
    }
    job = loads_job(json.dumps(document), base_dir=tmp_path)
    document["case"] = {
        "overrides": {
            str(n.result.measurement_id): bit for n in job.dag.nodes if n.result
        }
    }
    job = loads_job(json.dumps(document), base_dir=tmp_path)
    result = compile_job(job)
    assert (
        result.exit_state
        == replay(result.program, job.entry_state, job.device)
        == job.entry_state
    )
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
    restored = loads_job(json.dumps(job.document), base_dir=tmp_path)
    assert restored.entry_state == job.entry_state
    assert restored.document == job.document
    assert compile_job(restored).program == result.program


def test_explicit_input_never_invokes_initial_optimizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("explicit input invoked SA")

    monkeypatch.setattr(
        "nadit_physical_compiler.placement.enola_initial.place_initial", fail
    )
    document = job_document(tmp_path)
    job = loads_job(json.dumps(document), base_dir=tmp_path)
    assert job.document["entry"] == document["entry"]
    assert compile_job(job).exit_state == job.entry_state


def test_compiling_loaded_job_does_not_repeat_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = job_document(tmp_path)
    document["entry"] = {
        "kind": "enola_initial",
        "iterations": 2,
        "moves_per_iteration": 8,
    }
    job = loads_job(json.dumps(document), base_dir=tmp_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("compilation invoked initial SA again")

    monkeypatch.setattr(
        "nadit_physical_compiler.placement.enola_initial.place_initial", fail
    )
    assert compile_job(job).exit_state == job.entry_state
    assert compile_job(job).exit_state == job.entry_state


def test_array_capacity_and_ambiguity_are_explicit() -> None:
    device, _ = shared_device()
    with pytest.raises(UnsupportedJob, match="capacity"):
        make_enola_initial_layout(circuit(5, ""), device)
    duplicated = replace(
        device,
        pair_arrays=(device.pair_arrays[0], replace(device.pair_arrays[0], id="other")),
    )
    with pytest.raises(UnsupportedJob, match="array"):
        make_enola_initial_layout(circuit(4, ""), duplicated)
    assert (
        len(
            make_enola_initial_layout(
                circuit(4, ""), duplicated, array_id="cells"
            ).atoms
        )
        == 4
    )


def test_discovery_exposes_both_reference_providers() -> None:
    assert {"explicit", "enola_initial"} <= describe()["entry_providers"].keys()
    assert {"explicit", "enola_initial"} <= {
        item["properties"]["kind"]["const"]
        for item in job_schema()["properties"]["entry"]["oneOf"]
    }


@pytest.mark.parametrize("field", ["iterations", "moves_per_iteration"])
def test_entry_rejects_nonpositive_search_budgets(tmp_path: Path, field: str) -> None:
    document = job_document(tmp_path)
    document["entry"] = {"kind": "enola_initial", field: 0}
    with pytest.raises(ValueError, match="positive integers"):
        loads_job(json.dumps(document), base_dir=tmp_path)


def test_initialization_does_not_sample_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nadit_physical_compiler.execution import FakeBackend

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("initialization sampled a measurement")

    monkeypatch.setattr(FakeBackend, "measure", fail)
    document = job_document(tmp_path)
    (tmp_path / "c.qasm").write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; bit c; '
        "cz q[0],q[1]; c = measure q[0]; if (c == true) { cz q[0],q[3]; }",
        encoding="utf-8",
    )
    document["entry"] = {
        "kind": "enola_initial",
        "iterations": 2,
        "moves_per_iteration": 8,
    }
    assert (
        len(loads_job(json.dumps(document), base_dir=tmp_path).entry_state.atoms) == 4
    )
