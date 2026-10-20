"""Check the integrity and replay of actually executed full C++ traces."""

import json
from pathlib import Path
from typing import Any

import pytest

from reference.pipeline_reference import SOURCES, fixture_input
from reference.run_reference import COMMIT

REFERENCE = Path(__file__).parents[1] / "reference"
FIXTURES = json.loads(
    (REFERENCE / "pipeline_fixtures.json").read_text(encoding="utf-8")
)["cases"]
ORACLE = json.loads((REFERENCE / "pipeline_expected.json").read_text(encoding="utf-8"))
SUCCESSFUL = [
    case for case in FIXTURES if ORACLE["cases"][case["name"]]["status"] == "ok"
]


def test_pipeline_oracle_pins_every_executed_stage() -> None:
    """The portable data records exact sources, not regenerated expectations."""
    assert ORACLE["commit"] == COMMIT
    assert ORACLE["sources"] == {name: digest for name, (_, digest) in SOURCES.items()}
    assert set(ORACLE["cases"]) == {case["name"] for case in FIXTURES}
    assert len(ORACLE["translation_unit_sha256"]) == 64
    for case in FIXTURES:
        supplied = fixture_input(case)
        assert supplied.startswith(f"{len(case['slms'])}\n")
        assert f"\n{case['n_qubits']} {len(case['operations'])}\n" in supplied


@pytest.mark.parametrize("case", SUCCESSFUL, ids=lambda case: case["name"])
def test_original_complete_pipeline_trace_replays(case: dict[str, Any]) -> None:
    """Every generated movement reaches the final original placement."""
    result = ORACLE["cases"][case["name"]]["result"]
    layers = result["two_layers"]
    assert len(result["placements"]) == 2 * len(layers) + 1
    assert len(result["routings"]) == 2 * len(layers)
    assert len(result["single_layers"]) == len(layers) + 1
    assert len(result["reuse"]) == max(0, len(layers) - 1)
    assert len(result["initial"]) == case["n_qubits"]
    current = dict(enumerate(result["initial"]))
    carried: set[int] = set()
    for event in result["actions"]:
        atoms = set(event["atoms"])
        if event["kind"] == "load":
            assert not carried.intersection(atoms)
            carried.update(atoms)
        elif event["kind"] == "move":
            assert atoms <= carried
            current.update(zip(event["atoms"], event["targets"], strict=True))
        elif event["kind"] == "store":
            assert atoms <= carried
            carried.difference_update(atoms)
        else:
            assert not carried
    assert not carried
    by_id = {slm["id"]: slm for slm in case["slms"]}
    for atom, (slm_id, row, column) in enumerate(result["placements"][-1]):
        slm = by_id[slm_id]
        assert current[atom] == [
            slm["origin"][0] + column * slm["pitch"][0],
            slm["origin"][1] + row * slm["pitch"][1],
        ]
    assert sum(event["kind"] == "global_cz" for event in result["actions"]) == len(
        layers
    )


@pytest.mark.parametrize(
    ("name", "status"),
    (
        ("source_initial_slm_row_reset", "source_failure"),
        ("source_exhausted_multizone_window", "timeout"),
    ),
)
def test_original_failures_retain_completed_upstream_stages(
    name: str, status: str
) -> None:
    """A source failure remains an outcome instead of a fabricated trace."""
    record = ORACLE["cases"][name]
    assert record["status"] == status
    assert record["failed_stage"] == "placement"
    assert set(record["completed_stages"]) == {"scheduling", "reuse"}
    assert "result" not in record


def test_original_global_ry_extent_excludes_retained_gate_atom() -> None:
    """Preserve the observed source region mismatch as an explicit difference."""
    result = ORACLE["cases"]["global_ry_and_barrier"]["result"]
    global_zone = next(zone for zone in result["zones"] if zone["name"] == "global")
    max_y = global_zone["extent"][3]
    retained = result["reuse"][0]
    execution_exit = result["placements"][2]
    case = next(case for case in FIXTURES if case["name"] == "global_ry_and_barrier")
    slms = {slm["id"]: slm for slm in case["slms"]}
    assert retained
    for atom in retained:
        slm_id, row, _ = execution_exit[atom]
        slm = slms[slm_id]
        assert slm["origin"][1] + row * slm["pitch"][1] > max_y
    assert sum(action["kind"] == "global_ry" for action in result["actions"]) == 2


def test_original_multizone_cz_covers_both_declared_regions() -> None:
    """The complete source generator emits one multi-region pulse per layer."""
    result = ORACLE["cases"]["two_gate_zones"]["result"]
    pulses = [action for action in result["actions"] if action["kind"] == "global_cz"]
    assert len(pulses) == 2
    assert all(action["zones"] == [0, 1] for action in pulses)


def test_original_codegen_uses_declared_illumination_not_site_bounds() -> None:
    """Explicit input coverage survives the C++ adapter and original generator."""
    case = next(case for case in FIXTURES if case["name"] == "explicit_rydberg_extent")
    result = ORACLE["cases"][case["name"]]["result"]
    assert "\n1\n0 70 60 100\n" in fixture_input(case)
    assert result["zones"][0] == {
        "name": "zone_cz0",
        "extent": [0, 70, 60, 100],
    }
    assert result["zones"][0]["extent"] != [0, 80, 38, 80]
