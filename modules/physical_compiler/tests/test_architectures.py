"""Exact zoned/v1 reproduction and generated/file-based compilation equivalence."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_job import spec

from nadit_physical_compiler.architectures import generate
from nadit_physical_compiler.device import load_device, loads_device
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.provenance import canonical_hash
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.serialization import decode_result, encode_result
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]
PROFILES = tuple(path.stem for path in sorted((MODULE / "configs").glob("*.json")))


def parameters(profile: str = "multipair_flexible") -> dict[str, Any]:
    """Load explicit reproduction parameters, independent of the generator."""
    document: dict[str, Any] = json.loads(
        (MODULE / "examples/architectures" / f"{profile}.json").read_text()
    )
    params: dict[str, Any] = document["params"]
    return params


@pytest.mark.parametrize("profile", PROFILES)
def test_generated_device_exactly_equals_existing_configuration(profile: str) -> None:
    params = parameters(profile)
    original = deepcopy(params)
    first = generate("zoned/v1", params)
    assert params == original
    assert first == generate("zoned/v1", params)
    assert loads_device(json.dumps(first)) == load_device(
        MODULE / "configs" / f"{profile}.json"
    )
    assert (
        loads_device(json.dumps(first)).storage_capacity
        == params["storage"][0]["rows"] * params["storage"][0]["columns"]
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("bit", [0, 1])
def test_generated_and_file_jobs_have_identical_feedback_actions_and_final_states(
    profile: str, bit: int
) -> None:
    params = parameters(profile)
    document = spec(pair=profile == "one_pair_flexible")
    document["device"] = f"configs/{profile}.json"
    document.pop("gate_zones")
    document["strategy"]["transport"] = {"kind": "strict"}
    if profile != "one_pair_flexible":
        document["strategy"]["placement"] = {"kind": "ids"}
    preliminary = loads_job(json.dumps(document), base_dir=MODULE)
    document["case"]["overrides"] = {
        str(node.result.measurement_id): bit
        for node in preliminary.dag.nodes
        if node.result is not None
    }
    file_job = loads_job(json.dumps(document), base_dir=MODULE)
    document["device"] = {"architecture": {"family": "zoned/v1", "params": params}}
    generated = loads_job(json.dumps(document), base_dir=MODULE)
    assert generated.document["device"] == document["device"]
    assert generated.inputs == file_job.inputs
    actual = compile_job(generated)
    assert actual == compile_job(file_job)
    report = validate_result(
        actual,
        entry_state=generated.entry_state,
        device=generated.device,
        dag=generated.dag,
    )
    assert report["ok"], report
    assert (
        replay(actual.program, generated.entry_state, generated.device)
        == actual.exit_state
    )
    snapshot = encode_result(
        actual,
        entry_state=generated.entry_state,
        job=generated.document,
        device=generated.device,
        dag=generated.dag,
        inputs=generated.inputs,
        validation=report,
    )
    assert decode_result(snapshot) == actual
    assert all(record.value == bit for record in actual.measurements)
    legacy = replace(
        file_job.device,
        pair_arrays=(),
        zones=tuple(replace(zone, role=None) for zone in file_job.device.zones),
    )
    assert compile_job(replace(file_job, device=legacy)) == actual


def test_default_name_workspace_and_offset_coordinates_are_deterministic() -> None:
    params = parameters()
    params.pop("name")
    params["aod"].pop("workspace")
    params["storage"][0]["origin"] = {"x": 30, "y": 50}
    device = loads_device(json.dumps(generate("zoned/v1", params)))
    assert device.name == f"zoned/v1-{canonical_hash(params)[:12]}"
    assert device.site("storage_r0_c0").position.x == 30
    assert device.site("gate_0_left").position.y == 170
    assert all(
        device.aods[0].workspace.contains(site.position) for site in device.slm_sites
    )
    assert device.zones[2].bounds.y_min == 290


@pytest.mark.parametrize("preset", ["linear", "paper"])
def test_timing_presets_use_existing_distance_function(preset: str) -> None:
    params = parameters()
    params["timing"] = preset
    timing = loads_device(json.dumps(generate("zoned/v1", params))).timing
    assert timing.move_profile == preset
    assert timing.move_duration(110) == (110 if preset == "linear" else 200)


@pytest.mark.parametrize(
    "path,value",
    [
        (("storage", 0, "rows"), 0),
        (("storage", 0, "columns"), True),
        (("storage", 0, "pitch", "x"), -1),
        (("aod", "max_rows"), 0),
        (("aod", "workspace", "x_max"), 1),
        (("cz", "radius_um"), -1),
        (("gate_zones", 0, "separation"), 0),
        (("timing",), "unknown"),
        (("unknown",), 1),
        (("storage", 0, "bogus"), 1),
    ],
)
def test_invalid_parameters_report_parameter_path(
    path: tuple[str | int, ...], value: Any
) -> None:
    params = parameters()
    target: Any = params
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match="params"):
        generate("zoned/v1", params)


def test_overlap_requires_explicit_declaration_and_pair_geometry_is_checked() -> None:
    params = parameters()
    params["storage"][0]["bounds"]["y_max"] = 120
    with pytest.raises(ValueError, match="shared_zones"):
        generate("zoned/v1", params)
    params["shared_zones"] = [["storage", "entanglement"]]
    assert generate("zoned/v1", params)["pair_arrays"]
    params["gate_zones"][0]["pitch"]["x"] = 5
    with pytest.raises(ValueError, match="different pairs"):
        generate("zoned/v1", params)


def test_unknown_family_and_insufficient_initial_storage() -> None:
    with pytest.raises(ValueError, match="architecture.family"):
        generate("missing/v1", parameters())
    params = parameters()
    params["storage"][0].update(rows=1, columns=1)
    params.pop("site_order")
    document = spec()
    document["device"] = {"architecture": {"family": "zoned/v1", "params": params}}
    document.pop("gate_zones")
    with pytest.raises(ValueError, match="1 available"):
        loads_job(json.dumps(document), base_dir=MODULE)


def test_multiple_storage_blocks_and_gate_zones_use_roles_and_declared_pairs() -> None:
    params = parameters()
    params.pop("site_order")
    params["aod"].pop("workspace")
    storage = deepcopy(params["storage"][0])
    storage.update(id="memory2", prefix="memory2", origin={"x": 30, "y": 0})
    params["storage"].append(storage)
    gates = deepcopy(params["gate_zones"][0])
    gates.update(
        id="gates2",
        position={"x": 0, "y": 180},
        left_prefix="gates2_left",
        right_prefix="gates2_right",
    )
    gates.pop("left_ids")
    gates.pop("right_ids")
    params["gate_zones"].append(gates)
    document = spec()
    document.pop("gate_zones")
    document["device"] = {"architecture": {"family": "zoned/v1", "params": params}}
    document["strategy"]["placement"] = {"kind": "ids"}
    document["strategy"]["transport"] = {"kind": "strict"}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert job.device.storage_zone_ids == ("storage", "memory2")
    assert job.device.storage_capacity == 24
    assert job.catalog is not None and job.catalog.capacity == 4
    assert len(job.catalog.regions) == 2
    assert job.document["strategy"]["placement"]["storage_zone_id"] == [
        "storage",
        "memory2",
    ]
    result = compile_job(job)
    assert validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )["ok"]
    assert replay(result.program, job.entry_state, job.device) == result.exit_state
