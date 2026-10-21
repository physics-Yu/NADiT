"""Compile and replay a two-atom circuit with one measurement-feedback branch."""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from nadit_physical_compiler import FakeBackend, replay
from nadit_physical_compiler.job import compile_job, load_job
from nadit_physical_compiler.metrics import collect_metrics


def main() -> None:
    """Print compilation metrics and Stim-style actions for a seeded or forced case."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bit", type=int, choices=(0, 1))
    args = parser.parse_args()
    job = load_job(Path(__file__).resolve().parent / "jobs" / "pair.json")
    dag, device, entry = job.dag, job.device, job.entry_state
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
                "layers": len(result.layers),
                "atom_steps": metrics["atom_steps"],
                "program_duration_us": metrics["program_duration_us"],
                "result_completion_us": metrics["duration_us"],
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
            },
            indent=2,
        )
    )
    print(result.program.to_text(), end="")


if __name__ == "__main__":
    main()
