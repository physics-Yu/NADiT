"""Device-owned interaction arrays, role defaults, and snapshot round trips."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_job import spec

from nadit_physical_compiler.device import Zone, loads_device
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    MultiPairPlacer,
)
from nadit_physical_compiler.serialization import encode_device

MODULE = Path(__file__).resolve().parents[1]


def device_document() -> dict[str, Any]:
    """Return the smallest existing comparison Device with pair metadata."""
    document: dict[str, Any] = json.loads(
        (MODULE / "configs/multipair_flexible.json").read_text()
    )
    return document


def test_pair_grids_and_reference_snapshots_resolve_the_same_pairs() -> None:
    raw = device_document()
    expected = loads_device(json.dumps(raw))
    raw.pop("slm_sites")
    raw["pair_arrays"] = [
        {
            "id": "gates",
            "zone": "entanglement",
            **{
                side: {
                    "prefix": side,
                    "origin": {"x": x, "y": 120},
                    "rows": 1,
                    "columns": 2,
                    "pitch": {"x": 8, "y": 10},
                }
                for side, x in (("left", 0), ("right", 2))
            },
        }
    ]
    actual = loads_device(json.dumps(raw))
    assert [
        site.position
        for pair in GatePairCatalog.from_pair_arrays(actual).pairs
        for site in pair
    ] == [
        site.position
        for pair in GatePairCatalog.from_pair_arrays(expected).pairs
        for site in pair
    ]
    snapshot = encode_device(actual)
    assert snapshot["pair_arrays"][0]["left"] == [["left_r0_c0", "left_r0_c1"]]
    assert loads_device(json.dumps(snapshot)) == actual


@pytest.mark.parametrize(
    "change", ["shape", "duplicate", "unknown", "zone", "far", "interacting", "no_cz"]
)
def test_invalid_pair_arrays_fail_at_device_loading(change: str) -> None:
    raw = device_document()
    pairs = raw["pair_arrays"][0]
    if change == "shape":
        pairs["right"] = [["gate_0_right"]]
    elif change == "duplicate":
        pairs["right"][0][0] = "gate_0_left"
    elif change == "unknown":
        pairs["left"][0][0] = "missing"
    elif change == "zone":
        pairs["zone"] = "storage"
    elif change == "far":
        raw["cz"]["radius_um"] = 1
    elif change == "interacting":
        raw["cz"]["radius_um"] = 7
    else:
        raw["cz"] = None
    with pytest.raises(ValueError, match="pair_arrays"):
        loads_device(json.dumps(raw))


def test_arrays_in_one_zone_preserve_groups_without_duplicate_illumination() -> None:
    raw = device_document()
    original = raw["pair_arrays"][0]
    raw["pair_arrays"] = [
        {
            "id": f"g{i}",
            "zone": "entanglement",
            "left": [[original["left"][0][i]]],
            "right": [[original["right"][0][i]]],
        }
        for i in range(2)
    ]
    device = loads_device(json.dumps(raw))
    catalog = GatePairCatalog.from_pair_arrays(device)
    assert catalog.capacity == 2
    assert len(catalog.region_pairs) == 2
    assert len(catalog.regions) == 1


@pytest.mark.parametrize("kind", ["multipair", "ids", "astar"])
def test_storage_role_resolves_omitted_option_and_explicit_id_wins(kind: str) -> None:
    raw = device_document()
    raw["zones"][0]["id"] = "memory"
    document = spec()
    document["device"] = raw
    document.pop("gate_zones")
    document["strategy"]["placement"] = {"kind": kind}
    job = loads_job(json.dumps(document), base_dir=MODULE)
    assert job.document["strategy"]["placement"]["storage_zone_id"] == "memory"
    assert job.device.storage_capacity == 12
    assert job.catalog == GatePairCatalog.from_pair_arrays(job.device)
    if kind == "multipair":
        assert MultiPairPlacer("transport", job.catalog).storage_zone_id is None
    else:
        assert (
            HeuristicPlacer("transport", job.catalog).initial_layout(
                4, job.device, atom_ids=job.entry_state.qubit_to_atom
            )
            == job.entry_state
        )
    raw["zones"].append({**raw["zones"][0], "id": "chosen", "role": None})
    document["strategy"]["placement"]["storage_zone_id"] = "chosen"
    explicit = loads_job(json.dumps(document), base_dir=MODULE)
    assert explicit.document["strategy"]["placement"]["storage_zone_id"] == "chosen"
    assert compile_job(explicit) == compile_job(job)


def test_explicit_catalogue_overrides_device_pairs_including_empty_override() -> None:
    document = spec(pair=True)
    declared = loads_job(json.dumps(document), base_dir=MODULE)
    document.pop("gate_zones")
    implicit = loads_job(json.dumps(document), base_dir=MODULE)
    assert declared.catalog == implicit.catalog
    document["gate_zones"] = []
    assert loads_job(json.dumps(document), base_dir=MODULE).catalog is None


def test_legacy_roles_and_unknown_role() -> None:
    raw = device_document()
    legacy = deepcopy(raw)
    legacy.pop("pair_arrays")
    for zone in legacy["zones"]:
        zone.pop("role")
    old = loads_device(json.dumps(legacy))
    new = loads_device(json.dumps(raw))
    assert old.storage_zone_ids == new.storage_zone_ids == ("storage",)
    assert (
        replace(
            new, pair_arrays=(), zones=tuple(replace(z, role=None) for z in new.zones)
        )
        == old
    )
    with pytest.raises(ValueError, match="role"):
        Zone("bad", new.zones[0].bounds, (), "unknown")  # type: ignore[arg-type]
