"""Search Smarter can restore explicit homes without storage optimization."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_job import MODULE, spec

from nadit_physical_compiler.atom_ir import AtomProgram
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.contracts import AtomFragment, GateLayer
from nadit_physical_compiler.device import Device
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.job import CompilationJob, compile_job, loads_job
from nadit_physical_compiler.physical_ir import PhysicalDAG
from nadit_physical_compiler.placement import HeuristicPlacer
from nadit_physical_compiler.placement.reference import ReferencePlacer
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.schemas.job import job_schema
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.state import AtomLayout, AtomState
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.validation import validate_result


def qec_job(
    tmp_path: Path,
    placement: str = "ids",
    transport: str = "strict",
    bit: int = 1,
    *,
    rigid: bool = False,
) -> CompilationJob:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    document = base.document
    circuit = tmp_path / "qec.qasm3"
    circuit.write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; bit[2] m; '
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "cz q[0],q[2]; cz q[1],q[3]; barrier q; "
        "cz q[0],q[1]; cz q[2],q[3]; barrier q; "
        "m[0]=measure q[0]; m[1]=measure q[2]; reset q[0]; "
        "if (m[0] == true) { cz q[0],q[1]; } "
        "if (m[1] == true) { rx(0.2) q[2]; }",
        encoding="utf-8",
    )
    document["circuit"]["qasm"] = str(circuit)
    document["device"] = encode_device(base.device)
    document["device"]["readout"]["max_atoms"] = 1 if rigid else None
    if rigid:
        document["device"]["aods"][0].update(
            motion="rigid", max_rows=16, max_columns=16
        )
    document["strategy"].update(
        kind="search_smarter",
        mode="qec",
        placement={"kind": placement},
        transport={"kind": transport},
        reuse={"kind": "matching"},
    )
    document["entry"] = {
        "kind": "explicit",
        "atoms": [
            [17, "storage_r2_c3", 0],
            [3, "storage_r0_c0", 1],
            [90, "storage_r1_c2", 2],
            [41, "storage_r2_c0", 3],
        ],
    }
    preview = loads_job(json.dumps(document), base_dir=MODULE)
    document["case"]["overrides"] = {
        str(n.result.measurement_id): bit for n in preview.dag.nodes if n.result
    }
    return loads_job(json.dumps(document), base_dir=MODULE)


class RecordingStrategy:
    """Observe actual layer boundaries without changing selection or lowering."""

    def __init__(self, inner: SearchSmarterStrategy, home: AtomLayout) -> None:
        self.inner, self.home = inner, home
        self.fragments: list[AtomFragment] = []

    @property
    def lookahead_enabled(self) -> bool:
        return self.inner.lookahead_enabled

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        return self.inner.select(dag, ready)

    def lower(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> AtomFragment:
        assert layout == self.home
        fragment = self.inner.lower(
            dag,
            layer,
            layout,
            device,
            following=following,
            retained_atoms=retained_atoms,
        )
        assert fragment.exit_layout == self.home
        assert fragment.retained_atoms == ()
        assert replay(AtomProgram(fragment.steps), layout, device) == self.home
        self.fragments.append(fragment)
        return fragment


@pytest.mark.parametrize("placement", ["ids", "astar"])
@pytest.mark.parametrize("transport", ["strict", "staged", "relaxed"])
@pytest.mark.parametrize("bit", [0, 1])
def test_qec_layers_restore_exact_user_mapping(
    tmp_path: Path, placement: str, transport: str, bit: int
) -> None:
    job = qec_job(tmp_path, placement, transport, bit)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.placer, ReferencePlacer)
    assert job.document["strategy"]["reuse"] == {"kind": "none"}
    assert not job.strategy.lookahead_enabled
    observed = RecordingStrategy(job.strategy, job.entry_state)
    result = compile_physical(
        job.dag,
        job.device,
        strategy=observed,
        entry_state=job.entry_state,
        measurement_backend=job.measurement_backend,
    )
    assert len(observed.fragments) == len(result.layers)
    assert result.exit_state == job.entry_state
    assert replay(result.program, job.entry_state, job.device) == job.entry_state
    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"], report
    assert len(result.measurements) == 2
    records = {m.result: m for m in result.measurements}
    operations = {o.operation_id: o for o in result.operations}
    for node in job.dag.nodes:
        if node.condition:
            assert operations[node.id].status == ("emitted" if bit else "skipped")
            if bit:
                assert (
                    operations[node.id].start_us
                    >= records[node.condition.result].available_at_us
                )


@pytest.mark.parametrize("placement", ["ids", "astar"])
def test_qec_does_not_run_storage_search_or_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placement: str
) -> None:
    from nadit_physical_compiler.placement import heuristic
    from nadit_physical_compiler.reuse import MatchingReuseAnalyzer

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fixed reference invoked adaptive storage/reuse")

    job = qec_job(tmp_path, placement)
    monkeypatch.setattr(heuristic, "storage_jobs", fail)
    monkeypatch.setattr(MatchingReuseAnalyzer, "analyze", fail)
    assert compile_job(job).exit_state == job.entry_state


def test_default_and_explicit_adaptive_mode_preserve_existing_program(
    tmp_path: Path,
) -> None:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    original = compile_job(base)
    document["strategy"]["mode"] = "adaptive"
    document["strategy"]["kind"] = "search_smarter"
    named = loads_job(json.dumps(document), base_dir=MODULE)
    assert compile_job(named) == original
    assert named.document["strategy"]["mode"] == "adaptive"
    assert named.document["strategy"]["kind"] == "search_smarter"
    assert "mode" not in base.document["strategy"]


@pytest.mark.parametrize("placement", ["pair", "multipair"])
def test_existing_simple_placers_support_reference_policy(placement: str) -> None:
    document = spec(pair=placement == "pair")
    document["strategy"]["mode"] = "qec"
    job = loads_job(json.dumps(document), base_dir=MODULE)
    result = compile_job(job)
    assert result.exit_state == job.entry_state
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]


def test_rigid_strict_and_singleton_measurement_return(tmp_path: Path) -> None:
    job = qec_job(tmp_path, rigid=True)
    result = compile_job(job)
    assert result.exit_state == job.entry_state
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]


def test_relocated_local_operations_restore_homes(tmp_path: Path) -> None:
    job = qec_job(tmp_path)
    document = job.document
    path = tmp_path / "local.qasm3"
    path.write_text(
        'OPENQASM 3.0; include "stdgates.inc"; qubit[4] q; '
        "rx(0.2) q[0]; rx(0.2) q[1]; reset q[0];",
        encoding="utf-8",
    )
    document["circuit"]["qasm"] = str(path)
    document["case"]["overrides"] = {}
    document["device"] = encode_device(job.device)
    for zone in document["device"]["zones"]:
        if zone["id"] == "storage":
            zone["operations"] = ["load", "store"]
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    observed = RecordingStrategy(job.strategy, job.entry_state)
    result = compile_physical(
        job.dag, job.device, strategy=observed, entry_state=job.entry_state
    )
    assert result.exit_state == job.entry_state
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]


def test_python_mode_and_job_roundtrip(tmp_path: Path) -> None:
    job = qec_job(tmp_path)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.placer, ReferencePlacer)
    assert isinstance(job.strategy.placer.base, HeuristicPlacer)
    direct = replace(job.strategy, placer=job.strategy.placer.base, mode="qec")
    result = compile_physical(
        job.dag,
        job.device,
        strategy=direct,
        entry_state=job.entry_state,
        measurement_backend=job.measurement_backend,
    )
    assert result == compile_job(job)
    restored = loads_job(json.dumps(job.document), base_dir=MODULE)
    assert restored.document == job.document
    assert compile_job(restored) == result


def test_cli_and_discovery_expose_qec_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = qec_job(tmp_path)
    path, output = tmp_path / "job.json", tmp_path / "result.json"
    path.write_text(json.dumps(job.document), encoding="utf-8")
    assert main(["compile", str(path), "--validate", "-o", str(output)]) == 0
    capsys.readouterr()
    assert main(["validate", str(output)]) == 0
    assert "qec" in describe()["compiler_strategies"]["search_smarter"]["modes"]
    schema = job_schema()["properties"]["strategy"]["oneOf"][0]
    assert schema["properties"]["mode"]["enum"] == ["adaptive", "qec"]


def test_invalid_mode_has_a_configuration_error() -> None:
    document = spec()
    document["strategy"]["mode"] = "unknown"
    with pytest.raises(ValueError, match="strategy.mode"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_global_rotation_restores_participants_and_displaced_spectators() -> None:
    from test_global_rotation_placement import _dag, _device, _layout

    from nadit_physical_compiler.placement import PairPlacer
    from nadit_physical_compiler.scheduling import ReadyScheduler
    from nadit_physical_compiler.transport import SequentialTransport

    device = _device()
    home = _layout(device, "g0", "s0", "outside")
    dag = _dag((0,), 3)
    observed = RecordingStrategy(
        SearchSmarterStrategy(
            ReadyScheduler(),
            PairPlacer("transport"),
            SequentialTransport("transport"),
            mode="qec",
        ),
        home,
    )
    result = compile_physical(dag, device, strategy=observed, entry_state=home)
    assert validate_result(result, entry_state=home, device=device, dag=dag)["ok"]
    assert len(observed.fragments) == 1


def test_documented_qec_job() -> None:
    from nadit_physical_compiler.job import load_job

    job = load_job(MODULE / "examples/jobs/search-smarter-qec.json")
    result = compile_job(job)
    assert result.exit_state == job.entry_state
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
