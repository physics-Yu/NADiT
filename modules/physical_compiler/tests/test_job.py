"""Declarative jobs preserve the public Python compositions and input identities."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import pytest

from nadit_physical_compiler import FakeBackend, compile_physical
from nadit_physical_compiler.atom_ir import AtomProgram, AtomStep, CZPulse
from nadit_physical_compiler.contracts import Transporter
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.job import compile_job, load_job, loads_job
from nadit_physical_compiler.placement import (
    HeuristicPlacer,
    MultiPairPlacer,
    PairPlacer,
    PlacementConfig,
)
from nadit_physical_compiler.registry import REGISTRY, build_strategy
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.serialization import encode_device, encode_program
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.support import UnsupportedJob
from nadit_physical_compiler.transport import (
    PairTransport,
    RelaxedTransport,
    SequentialTransport,
    StagedStrictTransport,
    StrictTransport,
)

MODULE = Path(__file__).resolve().parents[1]


def spec(*, pair: bool = False) -> dict[str, Any]:
    """Return the existing example's declarative inputs, without tuned defaults."""
    return {
        "schema": "nadit.job/1",
        "circuit": {
            "qasm": "examples/pair_feedback.qasm3"
            if pair
            else "examples/multipair_feedback.qasm3"
        },
        "device": "configs/one_pair_flexible.json"
        if pair
        else "configs/multipair_flexible.json",
        "aod": "transport",
        "gate_zones": [
            {
                "zone": "entanglement",
                "pairs": [
                    ["gate_0_left", "gate_0_right"],
                    ["gate_1_left", "gate_1_right"],
                ],
            }
        ],
        "strategy": {
            "scheduler": {"kind": "ready", "max_cz_gates": 1 if pair else "catalog"},
            "placement": {"kind": "pair" if pair else "multipair"},
            "transport": {"kind": "pair" if pair else "sequential"},
            "emitter": {"kind": "layer" if pair else "whole_layer"},
            "reuse": {"kind": "none"},
        },
        "entry": {
            "kind": "explicit",
            "atoms": [[7, "storage_r0_c0", 0], [11, "storage_r0_c1", 1]],
        }
        if pair
        else {"kind": "initial_storage", "atom_ids": [7, 10, 13, 16]},
        "case": {"seed": 17},
    }


@pytest.mark.parametrize("bit", [0, 1])
def test_pair_matches_python_composition(bit: int) -> None:
    document = spec(pair=True)
    preliminary = loads_job(json.dumps(document), base_dir=MODULE)
    overrides = {
        node.result: bit for node in preliminary.dag.nodes if node.result is not None
    }
    document["case"]["overrides"] = {
        str(ref.measurement_id): value for ref, value in overrides.items()
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)
    expected = compile_physical(
        job.dag,
        job.device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
        ),
        entry_state=job.entry_state,
        measurement_backend=FakeBackend(17, overrides),
    )
    assert compile_job(job) == expected


@pytest.mark.parametrize("placement", ["multipair", "ids", "astar"])
@pytest.mark.parametrize("transport", ["sequential", "strict", "staged", "relaxed"])
@pytest.mark.parametrize("reuse", [False, True])
def test_multipair_matches_python_composition(
    placement: Literal["multipair", "ids", "astar"], transport: str, reuse: bool
) -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": placement}
    document["strategy"]["transport"] = {"kind": transport}
    document["strategy"]["reuse"] = {"kind": "matching" if reuse else "none"}
    document["case"]["overrides"] = {"12": 1}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert job.catalog is not None
    placer = (
        MultiPairPlacer("transport", job.catalog)
        if placement == "multipair"
        else HeuristicPlacer(
            "transport", job.catalog, config=PlacementConfig.for_method(placement)
        )
    )
    transports: dict[str, Transporter] = {
        "sequential": SequentialTransport("transport"),
        "strict": StrictTransport("transport"),
        "staged": StagedStrictTransport("transport"),
        "relaxed": RelaxedTransport("transport"),
    }
    expected = compile_physical(
        job.dag,
        job.device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(job.catalog.capacity),
            placer,
            transports[transport],
            WholeLayerEmitter(job.catalog.coverage),
            MatchingReuseAnalyzer() if reuse else None,
        ),
        entry_state=job.entry_state,
        measurement_backend=job.measurement_backend,
    )
    assert compile_job(job) == expected


def test_defaults_and_paths_are_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = spec()
    document["circuit"]["qasm"] = str(MODULE / document["circuit"]["qasm"])
    document["device"] = str(MODULE / document["device"])
    document["strategy"]["placement"] = {"kind": "astar", "queue_capacity": 11}
    path = tmp_path / "job.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    job = load_job(path)
    placement = job.document["strategy"]["placement"]
    assert placement["deepening_factor"] == 0.8
    assert placement["deepening_value"] == 0.2
    assert placement["lookahead_factor"] == 0.2
    assert placement["queue_capacity"] == 11
    assert job.document["strategy"]["scheduler"]["max_cz_gates"] == 2
    assert job.document["circuit"]["gate_set"] == ["rx", "ry", "rz", "cz"]
    assert job.document["case"]["overrides"] == {}
    assert (
        loads_job(json.dumps(job.document), base_dir=tmp_path).entry_state
        == job.entry_state
    )


def test_inline_device_and_reverse_explicit_atom_order() -> None:
    document = spec(pair=True)
    original = loads_job(json.dumps(document), base_dir=MODULE)
    document["device"] = encode_device(original.device)
    document["entry"]["atoms"].reverse()
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert job.device == original.device
    assert job.entry_state.qubit_to_atom == (7, 11)
    assert tuple(atom.id for atom in job.entry_state.atoms) == (11, 7)


@pytest.mark.parametrize(
    "path,value",
    [
        (("unexpected",), 1),
        (("circuit", "extra"), 1),
        (("strategy", "placement", "bogus"), 1),
        (("strategy", "placement", "kind"), "missing"),
        (("strategy", "scheduler", "max_cz_gates"), True),
        (("case", "overrides"), {"99": 1}),
        (("case", "overrides"), {"12": 2}),
        (("schema",), "nadit.job/2"),
    ],
)
def test_invalid_input_reports_path(path: tuple[str, ...], value: Any) -> None:
    document = spec()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="\\.".join(path)):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_every_registered_kind_constructs(tmp_path: Path) -> None:
    document = spec()
    qasm = tmp_path / "static.qasm"
    qasm.write_text(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[4]; cz q[0],q[1]; cz q[2],q[3];',
        encoding="utf-8",
    )
    document["circuit"]["qasm"] = str(qasm)
    base = loads_job(json.dumps(document), base_dir=MODULE)
    for stage, implementations in REGISTRY.items():
        for kind in implementations:
            strategy = json.loads(json.dumps(document["strategy"]))
            strategy[stage] = {"kind": kind}
            if kind == "pair" and stage in ("placement", "transport"):
                with pytest.raises(UnsupportedJob, match="at most two"):
                    build_strategy(
                        strategy,
                        dag=base.dag,
                        device=base.device,
                        aod_id="transport",
                        catalog=base.catalog,
                    )
                continue
            built, resolved = build_strategy(
                strategy,
                dag=base.dag,
                device=base.device,
                aod_id="transport",
                catalog=base.catalog,
            )
            assert resolved[stage]["kind"] == kind
            assert isinstance(built, SearchSmarterStrategy)


def test_explicit_transport_geometry_roundtrips() -> None:
    from nadit_physical_compiler.transport.staging_geometry import StagingGeometry

    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    geometry = asdict(StagingGeometry.from_device(base.device, "transport"))
    geometry["coordinate_quantum"] = 1
    document["strategy"]["transport"] = {"kind": "staged", "geometry": geometry}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.transporter, StagedStrictTransport)
    assert job.strategy.transporter.geometry is not None
    assert job.strategy.transporter.geometry.coordinate_quantum == 1
    assert job.document["strategy"]["transport"]["geometry"] == geometry


def test_explicit_placement_geometry_matches_public_constructor() -> None:
    from nadit_physical_compiler.placement import PlacementGeometry

    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    assert base.catalog is not None
    geometry = PlacementGeometry.from_device(base.device, "transport", base.catalog)

    def encode_array(array: Any) -> dict[str, Any]:
        return {
            "id": array.id,
            "kind": array.kind,
            "pitch": asdict(array.pitch),
            "sites": [[site.id for site in row] for row in array.sites],
            "partner_sites": [[site.id for site in row] for row in array.partner_sites],
        }

    encoded = {
        "storage_arrays": [encode_array(array) for array in geometry.storage_arrays],
        "entanglement_arrays": [
            [encode_array(array) for array in pair]
            for pair in geometry.entanglement_arrays
        ],
    }
    document["strategy"]["placement"] = {
        "kind": "ids",
        "geometry": encoded,
        "trials": 2,
        "queue_capacity": 20,
        "reuse_level": 3.0,
        "use_window": False,
        "window_min_width": 8,
        "window_ratio": 0.5,
        "window_share": 0.7,
        "deepening_factor": 0.02,
        "deepening_value": 0.1,
        "lookahead_factor": 0.3,
        "max_nodes": 1000,
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.placer, HeuristicPlacer)
    assert job.strategy.placer.geometry == geometry
    assert job.strategy.placer.config == PlacementConfig(
        trials=2,
        queue_capacity=20,
        reuse_level=3.0,
        use_window=False,
        window_min_width=8,
        window_ratio=0.5,
        window_share=0.7,
        deepening_factor=0.02,
        deepening_value=0.1,
        lookahead_factor=0.3,
        max_nodes=1000,
    )
    assert compile_job(job).program.steps


@pytest.mark.parametrize("kind", ["region", "regions"])
def test_explicit_emitter_coverage(kind: str) -> None:
    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    assert base.catalog is not None
    coordinates = asdict(base.catalog.regions[0])
    coverage = (
        {"kind": "region", "region": coordinates}
        if kind == "region"
        else {"kind": "regions", "regions": [coordinates]}
    )
    if kind == "region":
        coverage = encode_program(
            AtomProgram(
                (
                    AtomStep(
                        (CZPulse("rydberg", base.catalog.regions[0], duration_us=1),)
                    ),
                )
            )
        )["steps"][0]["actions"][0]["coverage"]
    document["strategy"]["emitter"] = {"kind": "whole_layer", "coverage": coverage}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.emitter, WholeLayerEmitter)
    assert job.strategy.emitter.coverage.contains(base.catalog.pairs[0][0].position)
    assert compile_job(job).program.steps


def test_pair_explicit_entry_needs_no_catalog_or_regular_storage(
    tmp_path: Path,
) -> None:
    document = spec(pair=True)
    base = loads_job(json.dumps(document), base_dir=MODULE)
    device = encode_device(base.device)
    device.pop("cz")
    device.pop("pair_arrays")
    device["slm_sites"].append({"id": "irregular", "position": {"x": 19, "y": 2}})
    qasm = tmp_path / "local.qasm"
    qasm.write_text(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; rx(0.4) q[0];', encoding="utf-8"
    )
    document["circuit"]["qasm"] = str(qasm)
    document["device"] = device
    document["gate_zones"] = []
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert job.catalog is None
    assert compile_job(job).program.steps


def test_heuristic_explicit_entry_preserves_lazy_geometry(tmp_path: Path) -> None:
    document = spec(pair=True)
    base = loads_job(json.dumps(document), base_dir=MODULE)
    device = encode_device(base.device)
    device["slm_sites"].append({"id": "irregular", "position": {"x": 19, "y": 2}})
    qasm = tmp_path / "local.qasm"
    qasm.write_text(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; rx(0.4) q[0];', encoding="utf-8"
    )
    document["circuit"]["qasm"] = str(qasm)
    document["device"] = device
    document["strategy"]["placement"] = {"kind": "ids"}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.placer, HeuristicPlacer)
    assert job.strategy.placer.geometry is None
    assert compile_job(job).program.steps


def test_source_scheduler_and_reuse_compile_static_job(tmp_path: Path) -> None:
    document = spec()
    qasm = tmp_path / "static.qasm"
    qasm.write_text(
        'OPENQASM 2.0; include "qelib1.inc"; qreg q[4]; '
        "cz q[0],q[1]; cz q[2],q[3]; cz q[0],q[2];",
        encoding="utf-8",
    )
    document["circuit"]["qasm"] = str(qasm)
    document["strategy"]["scheduler"] = {"kind": "source_asap"}
    document["strategy"]["reuse"] = {"kind": "source"}
    document["strategy"]["placement"] = {"kind": "ids"}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert compile_job(job).program.steps
    assert job.document["strategy"]["scheduler"]["max_filling_factor"] == 0.9


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue_capacity", True),
        ("queue_capacity", 1.5),
        ("use_window", 1),
        ("deepening_factor", "0.1"),
        ("method", "astar"),
        ("geometry", {"extra": 1}),
    ],
)
def test_search_option_errors_name_the_field(field: str, value: Any) -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": "ids", field: value}
    with pytest.raises(ValueError, match=f"strategy\\.placement\\.{field}"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_load_errors_are_input_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="job"):
        load_job(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="job"):
        loads_job("{", base_dir=tmp_path)
    document = spec()
    document["circuit"]["qasm"] = "missing.qasm"
    with pytest.raises(ValueError, match="circuit.qasm"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_relative_inputs_resolve_from_job_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = spec(pair=True)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "circuit.qasm3").write_text(
        (MODULE / "examples/pair_feedback.qasm3").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (inputs / "device.json").write_text(
        (MODULE / "configs/one_pair_flexible.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    document["circuit"]["qasm"] = "inputs/circuit.qasm3"
    document["device"] = "inputs/device.json"
    path = tmp_path / "job.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    job = load_job(path)
    assert job.document["circuit"]["qasm"] == str(inputs / "circuit.qasm3")
    assert job.document["device"] == str(inputs / "device.json")
    assert compile_job(job).program.steps


@pytest.mark.parametrize(
    "stage,kind,extra",
    [
        ("transport", "staged", {"geometry": {"sites": {}, "misspelled": 1}}),
        (
            "emitter",
            "whole_layer",
            {
                "coverage": {
                    "kind": "region",
                    "region": {
                        "x_min": 0,
                        "y_min": 0,
                        "x_max": 1,
                        "y_max": 1,
                        "misspelled": 1,
                    },
                }
            },
        ),
    ],
)
def test_nested_options_reject_unknown_keys(
    stage: str, kind: str, extra: dict[str, Any]
) -> None:
    document = spec()
    document["strategy"][stage] = {"kind": kind, **extra}
    with pytest.raises(ValueError, match=rf"strategy\.{stage}.*misspelled"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_default_numeric_types_match_constructor_json() -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": "ids"}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    configured = job.document["strategy"]["placement"]
    for name, value in asdict(PlacementConfig()).items():
        assert type(configured[name]) is type(value)
        assert configured[name] == value


def test_resolved_transport_geometry_fills_existing_default_without_mutation() -> None:
    from nadit_physical_compiler.transport.staging_geometry import StagingGeometry

    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    geometry = asdict(StagingGeometry.from_device(base.device, "transport"))
    del geometry["coordinate_quantum"]
    document["strategy"]["transport"] = {"kind": "staged", "geometry": geometry}
    original = deepcopy(document["strategy"])

    strategy, resolved = build_strategy(
        document["strategy"],
        dag=base.dag,
        device=base.device,
        aod_id="transport",
        catalog=base.catalog,
    )

    assert resolved["transport"]["geometry"]["coordinate_quantum"] is None
    assert document["strategy"] == original
    document["strategy"] = resolved
    restored = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(strategy.transporter, StagedStrictTransport)
    assert isinstance(restored.strategy, SearchSmarterStrategy)
    assert isinstance(restored.strategy.transporter, StagedStrictTransport)
    assert restored.strategy.transporter.geometry == strategy.transporter.geometry
    assert restored.document["strategy"] == resolved


def test_resolved_placement_geometry_fills_existing_array_defaults() -> None:
    from nadit_physical_compiler.placement import PlacementGeometry

    document = spec()
    base = loads_job(json.dumps(document), base_dir=MODULE)
    assert base.catalog is not None
    geometry = PlacementGeometry.from_device(base.device, "transport", base.catalog)

    def encode_array(array: Any) -> dict[str, Any]:
        encoded = {
            "id": array.id,
            "kind": array.kind,
            "pitch": asdict(array.pitch),
            "sites": [[site.id for site in row] for row in array.sites],
        }
        if array.partner_sites:
            encoded["partner_sites"] = [
                [site.id for site in row] for row in array.partner_sites
            ]
        return encoded

    encoded = {
        "storage_arrays": [encode_array(array) for array in geometry.storage_arrays],
        "entanglement_arrays": [
            [encode_array(array) for array in pair]
            for pair in geometry.entanglement_arrays
        ],
    }
    document["strategy"]["placement"] = {"kind": "ids", "geometry": encoded}
    original = deepcopy(document["strategy"])

    strategy, resolved = build_strategy(
        document["strategy"],
        dag=base.dag,
        device=base.device,
        aod_id="transport",
        catalog=base.catalog,
    )

    assert all(
        array["partner_sites"] == []
        for array in resolved["placement"]["geometry"]["storage_arrays"]
    )
    assert document["strategy"] == original
    document["strategy"] = resolved
    restored = loads_job(json.dumps(document), base_dir=MODULE)
    assert isinstance(strategy.placer, HeuristicPlacer)
    assert isinstance(restored.strategy, SearchSmarterStrategy)
    assert isinstance(restored.strategy.placer, HeuristicPlacer)
    assert restored.strategy.placer.geometry == strategy.placer.geometry == geometry
    assert restored.document["strategy"] == resolved


def test_resolved_null_geometry_preserves_constructor_inference() -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": "ids"}
    document["strategy"]["transport"] = {"kind": "staged"}

    job = loads_job(json.dumps(document), base_dir=MODULE)

    assert job.document["strategy"]["placement"]["geometry"] is None
    assert job.document["strategy"]["transport"]["geometry"] is None
    assert isinstance(job.strategy, SearchSmarterStrategy)
    assert isinstance(job.strategy.placer, HeuristicPlacer)
    assert isinstance(job.strategy.transporter, StagedStrictTransport)
    assert job.strategy.placer.geometry is None
    assert job.strategy.transporter.geometry is None


@pytest.mark.parametrize(
    "field,value",
    [("window_ratio", 0), ("window_share", -1), ("window_min_width", -1)],
)
def test_constructor_rejected_window_options_report_their_job_path(
    field: str, value: int
) -> None:
    document = spec()
    document["strategy"]["placement"] = {"kind": "ids", field: value}

    with pytest.raises(ValueError, match=rf"strategy\.placement\.{field}:"):
        loads_job(json.dumps(document), base_dir=MODULE)
