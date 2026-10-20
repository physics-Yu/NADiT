"""Adapt shared placement fixtures to public geometry/candidates and score nodes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Literal, cast

from nadit_physical_compiler.device import AOD, Position, Region, SLMSite, Timing
from nadit_physical_compiler.placement.candidates import gate_jobs, storage_jobs
from nadit_physical_compiler.placement.coordinates import build_coordinates
from nadit_physical_compiler.placement.geometry import PlacementGeometry, SLMArray
from nadit_physical_compiler.placement.initial import make_initial_layout
from nadit_physical_compiler.placement.scoring import (
    PlacementConfig,
    PlacementCoordinates,
    PlacementJob,
    _Node,
    _Search,
    search_placement,
)
from nadit_physical_compiler.state import AtomState, SLMHolder
from nadit_physical_compiler.transport.rules import StrictRules

HERE = Path(__file__).resolve().parent


def load_cases() -> tuple[dict[str, Any], ...]:
    """Load source-shared fixtures without executing a compiler."""
    data = json.loads((HERE / "placement_fixtures.json").read_text(encoding="utf-8"))
    return tuple(data["cases"])


def make_geometry(case: dict[str, Any]) -> PlacementGeometry:
    """Translate fixture SLM declarations to the same ordered geometric arrays."""
    grids = {}
    for spec in case["slms"]:
        grids[spec["id"]] = tuple(
            tuple(
                SLMSite(
                    f"{spec['id']}:{row}:{column}",
                    Position(
                        spec["origin"][0] + column * spec["pitch"][0],
                        spec["origin"][1] + row * spec["pitch"][1],
                    ),
                )
                for column in range(spec["columns"])
            )
            for row in range(spec["rows"])
        )
    storage = []
    paired: dict[int, list[dict[str, Any]]] = {}
    for spec in case["slms"]:
        if spec["kind"] == "storage":
            storage.append(
                SLMArray(
                    str(spec["id"]),
                    "storage",
                    grids[spec["id"]],
                    Position(*spec["pitch"]),
                )
            )
        else:
            paired.setdefault(spec["pair_group"], []).append(spec)
    gates = []
    for first, second in paired.values():
        gates.append(
            (
                SLMArray(
                    str(first["id"]),
                    "left",
                    grids[first["id"]],
                    Position(*first["pitch"]),
                    grids[second["id"]],
                ),
                SLMArray(
                    str(second["id"]),
                    "right",
                    grids[second["id"]],
                    Position(*second["pitch"]),
                    grids[first["id"]],
                ),
            )
        )
    return PlacementGeometry(tuple(storage), tuple(gates))


def _site_key(site: SLMSite) -> list[int]:
    return [int(item) for item in site.id.split(":")]


def _query_site(
    geometry: PlacementGeometry, indices: list[int]
) -> tuple[SLMArray, int, int]:
    return geometry.locate(":".join(map(str, indices)))


def _site_at(geometry: PlacementGeometry, indices: list[int]) -> SLMSite:
    array, row, column = _query_site(geometry, indices)
    return array.site(row, column)


def _jobs(
    case: dict[str, Any], geometry: PlacementGeometry, config: PlacementConfig
) -> tuple[tuple[PlacementJob, ...], PlacementCoordinates, tuple[AtomState, ...]]:
    atoms = tuple(
        AtomState(index, site.position, SLMHolder(site.id))
        for index, indices in enumerate(case["placement"])
        for site in (_site_at(geometry, indices),)
    )
    gates = tuple((atoms[first], atoms[second]) for first, second in case["gates"])
    participating = tuple(atom for gate in gates for atom in gate)
    partners = {
        atom: atoms[other].position
        for first, second in case.get("next_gates", [])
        for atom, other in ((first, second), (second, first))
    }
    reuse = frozenset(case.get("next_reuse", []))
    if case["stage"] == "gate":
        occupied: set[str] = set()
        for atom in case.get("previous_reuse", []):
            array, row, column = _query_site(geometry, case["placement"][atom])
            occupied.update(site.id for site in array.gate_pair(row, column))
        pairs = tuple(
            left.gate_pair(row, column)
            for left, _ in geometry.entanglement_arrays
            for row in range(left.n_rows)
            for column in range(left.n_cols)
            if left.site(row, column).id not in occupied
        )
        targets = tuple(site for pair in pairs for site in pair)
        jobs = gate_jobs(
            gates,
            pairs,
            reuse_atoms=reuse,
            partner_positions=partners,
            config=config,
            geometry=geometry,
        )
        coordinates = build_coordinates(participating, targets, geometry, jobs)
    else:
        occupied = {
            _site_at(geometry, indices).id
            for indices in case["placement"]
            if _query_site(geometry, indices)[0].kind == "storage"
        }
        targets = tuple(
            site
            for array in geometry.storage_arrays
            for row in array.sites
            for site in row
            if site.id not in occupied
        )
        jobs = storage_jobs(
            participating,
            targets,
            reuse_atoms=reuse,
            partner_positions=partners,
            config=config,
            geometry=geometry,
        )
        coordinates = build_coordinates(participating, targets, geometry, jobs)
    return jobs, coordinates, atoms


def candidate_result(case: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Observe geometry, ordered candidates, and the original C++ score paths."""
    geometry = make_geometry(case)
    initial = make_initial_layout(case.get("initial_count", 0), geometry)
    result: dict[str, Any] = {
        "initial": [
            [int(value) for value in atom.holder.site_id.split(":")]
            for atom in initial.atoms
            if isinstance(atom.holder, SLMHolder)
        ],
        "nearest_storage": [],
        "nearest_entanglement": [],
        "nearest_entanglement_distance": [],
        "jobs": [],
        "scores": [],
        "final": [],
    }
    for query in case.get("nearest_storage_queries", []):
        point = _site_at(geometry, query).position
        array, row, column = geometry.nearest_storage(point)
        result["nearest_storage"].append([int(array.id), row, column])
    for query in case.get("nearest_entanglement_queries", []):
        first = _site_at(geometry, query[:3]).position
        second = _site_at(geometry, query[3:]).position
        array, row, column = geometry.nearest_entanglement(first, second)
        result["nearest_entanglement"].append([int(array.id), row, column])
        result["nearest_entanglement_distance"].append(
            geometry.nearest_entanglement_distance(first, second)
        )
    if case.get("stage", "none") == "none":
        return result
    config = PlacementConfig(
        **{"trials": 1, "queue_capacity": 30, **case.get("config", {})}
    )
    stage = cast(Literal["gate", "storage"], case["stage"])
    jobs, coordinates, atoms = _jobs(case, geometry, config)
    rules = StrictRules(
        AOD("reference", Region(-1000, -1000, 1000, 1000), 0.001),
        Timing(1, 1, 1),
    )
    search = _Search(jobs, rules, config, stage, coordinates)

    def ranked(point: Position, target: bool) -> list[int]:
        ranks = search.target_ranks if target else search.source_ranks
        return [ranks[0][point.y], ranks[1][point.x]]

    for job in jobs:
        atom_ids = [atom for atom, _ in job.options[0].assignments]
        result["jobs"].append(
            {
                "atoms": atom_ids,
                "current": [ranked(atoms[atom].position, False) for atom in atom_ids],
                "mean_lookahead": sum(option.lookahead_cost for option in job.options)
                / len(job.options),
                "options": [
                    {
                        "sites": (
                            [
                                ranked(site.position, True)
                                for _, site in option.assignments
                            ]
                            if option.moves
                            else [[0, 0]]
                        ),
                        "distances": list(option.distances) if option.moves else [0.0],
                        "lookahead": option.lookahead_cost,
                        "reuse": not bool(option.moves),
                    }
                    for option in job.options
                ],
            }
        )
    nodes: dict[tuple[int, ...], _Node] = {(): _Node()}
    for score in expected["scores"]:
        path = tuple(score["path"])
        if path not in nodes:
            parent = nodes[path[:-1]]
            for node in search.expand(parent):
                index = jobs[len(parent.chosen)].options.index(node.chosen[-1])
                nodes[(*path[:-1], index)] = node
        node = nodes[path]
        result["scores"].append(
            {
                "path": list(path),
                "cost": search.cost(node),
                "heuristic": search.heuristic(node),
                "scale": list(search.scale_factors),
            }
        )
    selected = search_placement(
        jobs, rules, config, stage=stage, coordinates=coordinates
    )
    final = {index: indices for index, indices in enumerate(case["placement"])}
    for option in selected:
        for atom, site in option.assignments:
            final[atom] = _site_key(site)
    result["final"] = [final[atom] for atom in range(len(atoms))]
    return result


def compare_result(expected: Any, actual: Any, path: str = "") -> None:
    """Compare exact discrete results and C++ float scores with rounding tolerance."""
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            raise ValueError(f"{path}: keys differ")
        for key, value in expected.items():
            compare_result(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            raise ValueError(f"{path}: lengths {len(expected)} != {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            compare_result(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not math.isclose(expected, actual, rel_tol=2e-6, abs_tol=2e-6):
            raise ValueError(f"{path}: {expected} != {actual}")
    elif expected != actual:
        raise ValueError(f"{path}: {expected} != {actual}")


def main() -> None:
    """Write candidate observations and compare all source-defined fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(
        (HERE / "placement_expected.json").read_text(encoding="utf-8")
    )["cases"]
    results = {}
    for case in load_cases():
        name = case["name"]
        results[name] = candidate_result(case, expected[name])
        if not case.get("known_difference"):
            compare_result(expected[name], results[name], name)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Compared {len(results)} placement cases (explicit source defect included).")


if __name__ == "__main__":
    main()
