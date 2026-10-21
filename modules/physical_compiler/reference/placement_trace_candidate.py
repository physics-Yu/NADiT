"""Extract actual pipeline placement stages and observe the shared local search."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal, cast

from nadit_physical_compiler.device import AOD, Region, Timing
from nadit_physical_compiler.placement.astar import a_star_tree_search
from nadit_physical_compiler.placement.ids import iterative_diving_search
from nadit_physical_compiler.placement.scoring import PlacementConfig, _Node, _Search
from nadit_physical_compiler.transport.rules import StrictRules
from reference.benchmark import paper_settings
from reference.placement_candidate import _jobs, make_geometry

type OptionKey = tuple[tuple[int, int], ...]
type JobOptionKeys = tuple[tuple[OptionKey, ...], ...]


def candidate_option_keys(search: _Search) -> JobOptionKeys:
    """Identify choices by physical assignment ranks, independent of tie order."""
    return tuple(
        tuple(
            tuple(
                (
                    search.target_ranks[0][site.position.y],
                    search.target_ranks[1][site.position.x],
                )
                for _, site in option.assignments
            )
            if option.moves
            else ()
            for option in job.options
        )
        for job in search.jobs
    )


def normalize_path(path: list[int], options: JobOptionKeys) -> tuple[OptionKey, ...]:
    """Resolve option indices to assignments so equal-key permutations stay visible."""
    return tuple(options[job][option] for job, option in enumerate(path))


def compare_physical_decisions(
    source: dict[str, Any], candidate: dict[str, Any], search: _Search
) -> dict[str, Any]:
    """Compare actual selected assignments and common-node g/h without tolerance."""
    source_options: JobOptionKeys = tuple(
        tuple(
            ()
            if option["reuse"]
            else tuple((site[0], site[1]) for site in option["sites"])
            for option in job["options"]
        )
        for job in source["result"]["jobs"]
    )
    local_options = candidate_option_keys(search)
    selected = [
        [event for event in trace["events"] if event["kind"] == "select"]
        for trace in (source, candidate)
    ]
    first_difference = next(
        (
            index
            for index, (left, right) in enumerate(zip(*selected, strict=False))
            if normalize_path(left["path"], source_options)
            != normalize_path(right["path"], local_options)
        ),
        None,
    )
    scores = [
        {
            normalize_path(event["path"], options): (event["cost"], event["heuristic"])
            for event in trace["events"]
            if event["kind"] == "score"
        }
        for trace, options in ((source, source_options), (candidate, local_options))
    ]
    shared = scores[0].keys() & scores[1].keys()
    return {
        "source_selections": len(selected[0]),
        "candidate_selections": len(selected[1]),
        "first_physical_selection_difference": first_difference,
        "same_selection_sequence": first_difference is None
        and len(selected[0]) == len(selected[1]),
        "common_physical_score_nodes": len(shared),
        "exact_score_differences": sum(
            scores[0][key] != scores[1][key] for key in shared
        ),
        "candidate_sets_match": all(
            set(left) == set(right)
            for left, right in zip(source_options, local_options, strict=True)
        ),
        "candidate_order_differing_jobs": sum(
            left != right
            for left, right in zip(source_options, local_options, strict=True)
        ),
    }


def extract_stage(
    source_input: dict[str, Any],
    source_trace: dict[str, Any],
    output_index: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one original gate/storage search with its original incoming state."""
    if output_index <= 0:
        raise ValueError("The initial placement is not a gate/storage search stage.")
    layer = (output_index - 1) // 2
    stage = "gate" if output_index % 2 else "storage"
    layers, reuse = source_trace["two_layers"], source_trace["reuse"]
    return {
        "name": f"{source_input['name']}_{config['method']}_{stage}_{layer}",
        "slms": [
            {**slm, "kind": "storage" if slm["pair_group"] < 0 else "gate"}
            for slm in source_input["slms"]
        ],
        "stage": stage,
        "placement": source_trace["placements"][output_index - 1],
        "gates": layers[layer],
        "next_gates": layers[layer + 1] if layer + 1 < len(layers) else [],
        "previous_reuse": reuse[layer - 1] if layer else [],
        "next_reuse": reuse[layer] if layer < len(reuse) else [],
        "config": config,
        "expected_final": source_trace["placements"][output_index],
        "aod": source_input.get("aod", {}),
        "provenance": {"placement_index": output_index, "cz_layer": layer},
    }


def first_layout_difference(
    source_trace: dict[str, Any], candidate_trace: dict[str, Any]
) -> int | None:
    """Return the first different full placement, keeping all prior stages fixed."""
    return next(
        (
            index
            for index, (source, candidate) in enumerate(
                zip(
                    source_trace["placements"],
                    candidate_trace["placements"],
                    strict=True,
                )
            )
            if source != candidate
        ),
        None,
    )


def make_search(case: dict[str, Any]) -> tuple[_Search, dict[int, list[int]]]:
    """Build the real candidate tree for a frozen stage, including incoming anchors."""
    geometry = make_geometry(case)
    config = PlacementConfig(
        **{
            key: value
            for key, value in case["config"].items()
            if key in PlacementConfig.__dataclass_fields__
        }
    )
    frozen = dict(case)
    final = {index: site for index, site in enumerate(case["placement"])}
    if case["stage"] == "gate":
        retained = {
            atom
            for atom in case["previous_reuse"]
            if geometry.locate(":".join(map(str, case["placement"][atom])))[0].kind
            != "storage"
        }
        frozen["previous_reuse"] = sorted(retained)
        frozen["gates"] = []
        for first, second in case["gates"]:
            anchored = (
                first if first in retained else second if second in retained else None
            )
            if anchored is None:
                frozen["gates"].append([first, second])
                continue
            array, row, column = geometry.locate(
                ":".join(map(str, case["placement"][anchored]))
            )
            other = second if anchored == first else first
            final[other] = [
                int(value) for value in array.partner_sites[row][column].id.split(":")
            ]
    jobs, coordinates, _ = _jobs(frozen, geometry, config)
    # Both source and local lookahead read partners from the incoming placement.
    rules = StrictRules(
        AOD(
            "reference",
            Region(-100000, -100000, 100000, 100000),
            **{"min_spacing": 2, **case.get("aod", {})},
        ),
        Timing(15, 15, 0),
    )
    return _Search(
        jobs,
        rules,
        config,
        cast(Literal["gate", "storage"], case["stage"]),
        coordinates,
    ), final


def observe_search(case: dict[str, Any]) -> dict[str, Any]:
    """Record score evaluations and selected nodes without replacing the search."""
    search, final = make_search(case)
    option_indices = [
        {id(option): index for index, option in enumerate(job.options)}
        for job in search.jobs
    ]
    events: list[dict[str, Any]] = []
    scores: dict[tuple[int, ...], tuple[float, float]] = {}

    def path(node: _Node) -> tuple[int, ...]:
        return tuple(
            option_indices[index][id(option)]
            for index, option in enumerate(node.chosen)
        )

    def heuristic(node: _Node) -> float:
        cost, value = search.cost(node), search.heuristic(node)
        key = path(node)
        scores[key] = cost, value
        events.append(
            {
                "kind": "score",
                "path": list(key),
                "cost": cost,
                "heuristic": value,
                "priority": cost + value,
            }
        )
        return value

    def is_goal(node: _Node) -> bool:
        key = path(node)
        done = len(key) == len(search.jobs)
        cost, value = scores[key]
        events.append(
            {
                "kind": "select",
                "path": list(key),
                "priority": cost + value,
                "goal": done,
            }
        )
        return done

    def expand(node: _Node) -> Any:
        events.append({"kind": "expand", "path": list(path(node))})
        return search.expand(node)

    config = search.config
    if config.method == "astar":
        selected = a_star_tree_search(
            _Node(),
            expand=expand,
            is_goal=is_goal,
            cost=search.cost,
            heuristic=heuristic,
            max_nodes=config.max_nodes,
        )
    else:
        selected = iterative_diving_search(
            _Node(),
            expand=expand,
            is_goal=is_goal,
            cost=search.cost,
            heuristic=heuristic,
            trials=config.trials,
            queue_capacity=config.queue_capacity,
        )
    for option in selected.chosen:
        for atom, site in option.assignments:
            final[atom] = [int(value) for value in site.id.split(":")]
    return {
        "scale": list(search.scale_factors),
        "jobs": [
            {
                "atoms": [atom for atom, _ in job.options[0].assignments],
                "mean_lookahead": search.mean_lookahead[index],
                "options": [
                    {
                        "sites": [
                            [int(value) for value in site.id.split(":")]
                            for _, site in option.assignments
                        ],
                        "distances": list(option.distances),
                        "lookahead": option.lookahead_cost,
                        "reuse": not bool(option.moves),
                    }
                    for option in job.options
                ],
            }
            for index, job in enumerate(search.jobs)
        ],
        "events": events,
        "selected": list(path(selected)),
        "final": [final[index] for index in range(len(final))],
    }


def main() -> None:
    """Extract divergences from saved runs, then optionally observe their searches."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observe", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for count, variant in (
        (60, "astar_strict"),
        (120, "ids_strict"),
        (180, "ids_strict"),
        (200, "ids_strict"),
    ):
        name = f"graphstate_{count}"
        source_input = json.loads((args.study / "inputs" / f"{name}.json").read_text())
        source = json.loads(
            (args.study / "runs" / f"{name}_source_{variant}.json").read_text()
        )["trace"]
        candidate = json.loads(
            (args.study / "runs" / f"{name}_local_{variant}.json").read_text()
        )["trace"]
        index = first_layout_difference(source, candidate)
        if index is None:
            continue
        case = extract_stage(source_input, source, index, paper_settings(variant))
        target = args.output / f"{case['name']}.json"
        target.write_text(json.dumps(case, indent=2) + "\n")
        if args.observe:
            observed = observe_search(case)
            target.with_name(target.stem + "_candidate.json").write_text(
                json.dumps(observed, indent=2) + "\n"
            )
        print(target)


if __name__ == "__main__":
    main()
