"""Compile a multi-CZ circuit with partner changes and measurement feedback."""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from nadit_physical_compiler import FakeBackend, SLMHolder, replay
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.metrics import collect_metrics
from nadit_physical_compiler.placement import HeuristicPlacer


def main() -> None:
    """Print a configured case's metrics and complete Stim-style atom program."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bit", type=int, choices=(0, 1))
    parser.add_argument("--capacity", type=int, choices=(1, 2), default=2)
    parser.add_argument("--placement", choices=("greedy", "ids"), default="greedy")
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--queue-capacity", type=int, default=100)
    parser.add_argument("--reuse-level", type=float, default=5.0)
    parser.add_argument("--window", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--window-min-width", type=int, default=16)
    parser.add_argument("--window-ratio", type=float, default=1.0)
    parser.add_argument("--window-share", type=float, default=0.8)
    parser.add_argument("--prefer-split", type=float, default=1.0)
    parser.add_argument(
        "--transport",
        choices=("sequential", "strict", "staged", "relaxed"),
        default="sequential",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Retain matched atoms for the next CZ layer.",
    )
    args = parser.parse_args()
    path = Path(__file__).resolve().parent / "jobs" / "multipair.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["gate_zones"][0]["pairs"] = document["gate_zones"][0]["pairs"][
        : args.capacity
    ]
    document["strategy"]["placement"] = (
        {
            "kind": "ids",
            "trials": args.trials,
            "queue_capacity": args.queue_capacity,
            "reuse_level": args.reuse_level,
            "use_window": args.window,
            "window_min_width": args.window_min_width,
            "window_ratio": args.window_ratio,
            "window_share": args.window_share,
        }
        if args.placement == "ids"
        else {"kind": "multipair"}
    )
    document["strategy"]["transport"] = {"kind": args.transport}
    if args.transport == "relaxed":
        document["strategy"]["transport"]["prefer_split"] = args.prefer_split
    document["strategy"]["reuse"] = {"kind": "matching" if args.reuse else "none"}
    job = loads_job(json.dumps(document), base_dir=path.parent)
    dag, device, entry = job.dag, job.device, job.entry_state
    catalog, placer = job.catalog, job.strategy.placer
    assert catalog is not None
    overrides = {
        reference: args.bit
        for node in dag.nodes
        if (reference := node.result) is not None and args.bit is not None
    }
    job = replace(
        job,
        document={
            **job.document,
            "case": {
                "seed": args.seed,
                "overrides": {
                    str(ref.measurement_id): bit for ref, bit in overrides.items()
                },
            },
        },
        measurement_backend=FakeBackend(args.seed, overrides),
    )
    result = compile_job(job)
    assert replay(result.program, entry, device) == result.exit_state
    metrics = collect_metrics(result)
    print(
        json.dumps(
            {
                "native_nodes": len(dag.nodes),
                "pair_capacity": catalog.capacity,
                "placement": args.placement,
                "initial_slm_sites": {
                    str(atom.id): atom.holder.site_id
                    for atom in entry.atoms
                    if isinstance(atom.holder, SLMHolder)
                },
                "placement_config": (
                    asdict(placer.config)
                    if isinstance(placer, HeuristicPlacer)
                    else None
                ),
                "transport": args.transport,
                "prefer_split": args.prefer_split
                if args.transport == "relaxed"
                else None,
                "reuse": args.reuse,
                "cz_gates_per_pulse": metrics["cz_gates_per_pulse"],
                "atom_steps": metrics["atom_steps"],
                "action_counts": metrics["action_counts"],
                "load_batch_sizes": metrics["load_batch_sizes"],
                "max_parallel_atoms": metrics["max_parallel_atoms"],
                "transport_duration_us": metrics["transport_duration_us"],
                "atom_load_count": metrics["atom_load_count"],
                "atom_store_count": metrics["atom_store_count"],
                "program_duration_us": metrics["program_duration_us"],
                "measurements": [
                    {
                        "source_id": item.result.measurement_id,
                        "value": item.value,
                        "available_at_us": item.available_at_us,
                    }
                    for item in result.measurements
                ],
                "skipped_native_ids": [
                    item.operation_id
                    for item in result.operations
                    if item.status == "skipped"
                ],
                "replay_matches_exit_state": True,
                "exit_slm_sites": {
                    str(atom.id): atom.holder.site_id
                    for atom in result.exit_state.atoms
                    if isinstance(atom.holder, SLMHolder)
                },
            },
            indent=2,
        )
    )
    print(result.program.to_text(), end="")


if __name__ == "__main__":
    main()
