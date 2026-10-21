"""Unsupported combinations are reported before any stage factory is called."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from test_job import spec

from nadit_physical_compiler.cli import main
from nadit_physical_compiler.job import loads_job
from nadit_physical_compiler.registry import REGISTRY, build_strategy
from nadit_physical_compiler.serialization import encode_device
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.transport.staging_geometry import StagingGeometry

MODULE = Path(__file__).resolve().parents[1]


def test_all_failed_stages_are_collected_before_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    document["strategy"]["placement"] = {"kind": "pair"}
    document["strategy"]["transport"] = {"kind": "pair"}

    def fail(*args: Any) -> Any:
        raise AssertionError("factory called before support admission")

    for kinds in REGISTRY.values():
        for kind, registration in tuple(kinds.items()):
            monkeypatch.setitem(kinds, kind, replace(registration, factory=fail))
    with pytest.raises(UnsupportedJob) as caught:
        build_strategy(
            document["strategy"],
            dag=base.dag,
            device=base.device,
            aod_id="transport",
            catalog=base.catalog,
        )
    assert [reason["stage"] for reason in caught.value.reasons] == [
        "strategy.placement",
        "strategy.transport",
    ]
    assert all("two" in reason["reason"] for reason in caught.value.reasons)


@pytest.mark.parametrize(
    "stage,kind",
    [
        ("placement", "ids"),
        ("placement", "astar"),
        ("scheduler", "source_asap"),
        ("transport", "staged"),
    ],
)
def test_irregular_arrays_report_support_reason(stage: str, kind: str) -> None:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    raw = encode_device(base.device)
    # One storage point breaks the rectangular lattice without changing gate pairs.
    next(site for site in raw["slm_sites"] if site["id"] == "storage_r0_c0")[
        "position"
    ]["x"] = 0.5
    document["device"] = raw
    document["strategy"][stage] = {"kind": kind}
    with pytest.raises(UnsupportedJob) as caught:
        loads_job(json.dumps(document), base_dir=MODULE)
    assert any(
        item["stage"] == f"strategy.{stage}" and "array" in item["reason"]
        for item in caught.value.reasons
    )


def test_explicit_staging_geometry_admits_otherwise_unrecognized_lattice() -> None:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    explicit = asdict(StagingGeometry.from_device(base.device, "transport"))
    raw = encode_device(base.device)
    next(site for site in raw["slm_sites"] if site["id"] == "storage_r0_c0")[
        "position"
    ]["x"] = 0.5
    document["device"] = raw
    document["entry"] = {
        "kind": "explicit",
        "atoms": [[q, f"storage_r1_c{q}", q] for q in range(4)],
    }
    document["strategy"]["transport"] = {"kind": "staged", "geometry": explicit}
    assert (
        loads_job(json.dumps(document), base_dir=MODULE).strategy.transporter
        is not None
    )


def test_missing_catalogue_checks_all_affected_stages() -> None:
    document = spec()
    document["gate_zones"] = []
    with pytest.raises(UnsupportedJob) as caught:
        loads_job(json.dumps(document), base_dir=MODULE)
    assert [item["stage"] for item in caught.value.reasons] == [
        "strategy.scheduler",
        "strategy.placement",
        "strategy.emitter",
    ]


def test_cli_unsupported_does_not_create_partial_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = spec()
    document["circuit"]["qasm"] = str(MODULE / document["circuit"]["qasm"])
    document["device"] = str(MODULE / document["device"])
    document["strategy"]["placement"] = {"kind": "pair"}
    document["strategy"]["transport"] = {"kind": "pair"}
    job = tmp_path / "job.json"
    job.write_text(json.dumps(document))
    result, program = tmp_path / "result.json", tmp_path / "program.txt"
    assert main(["compile", str(job), "-o", str(result), "--text", str(program)]) == 4
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(json.loads(captured.out)["reasons"]) == 2
    assert not result.exists() and not program.exists()


def test_syntax_error_is_not_reclassified_as_unsupported() -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": "ids", "geometry": {"unknown": 1}}
    with pytest.raises(ValueError, match="geometry.unknown") as caught:
        loads_job(json.dumps(document), base_dir=MODULE)
    assert not isinstance(caught.value, UnsupportedJob)


def test_source_scheduler_declares_its_existing_static_circuit_boundary() -> None:
    document = spec()
    document["strategy"]["scheduler"] = {"kind": "source_asap"}
    with pytest.raises(UnsupportedJob, match="use ready for measurement feedback"):
        loads_job(json.dumps(document), base_dir=MODULE)
