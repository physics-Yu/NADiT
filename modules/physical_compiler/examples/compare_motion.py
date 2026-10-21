"""Compare AOD capacity and motion using identical geometry and circuit cases."""

import argparse
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from nadit_physical_compiler import FakeBackend, replay
from nadit_physical_compiler.job import CompilationJob, compile_job, loads_job
from nadit_physical_compiler.metrics import collect_metrics
from nadit_physical_compiler.serialization import encode_result
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]
PROFILES = ("zoned_flexible", "zoned_flexible_16", "zoned_rigid_16")
CASES = (
    ("capacity", "motion_capacity.qasm3", None),
    ("feedback_0", "multipair_feedback.qasm3", 0),
    ("feedback_1", "multipair_feedback.qasm3", 1),
)


def study_job(
    profile: str,
    circuit: str,
    bit: int | None,
    *,
    seed: int = 17,
    placement: str = "ids",
    transport: str = "strict",
) -> CompilationJob:
    """Assemble one study job with the same gates, entry policy, and case.

    Args:
        profile: One of the three comparison configuration filenames.
        circuit: Circuit filename in this examples directory.
        bit: Explicit bit for all source measurement identities, or no override.
        seed: FakeBackend seed, shared by the compared devices.
        placement: Existing registry placement kind.
        transport: Existing registry transport kind.
    Returns:
        Resolved job; constructing it does not sample measurements.
    """
    document = {
        "schema": "nadit.job/1",
        "circuit": {"qasm": f"examples/{circuit}"},
        "device": f"configs/{profile}.json",
        "aod": "transport",
        "gate_zones": [
            {
                "zone": "entanglement",
                "pairs": [
                    [f"gate_left_r0_c{index}", f"gate_right_r0_c{index}"]
                    for index in range(20)
                ],
            }
        ],
        "strategy": {
            "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
            "placement": {"kind": placement},
            "transport": {"kind": transport},
            "emitter": {"kind": "whole_layer"},
            "reuse": {"kind": "matching"},
        },
        "entry": {"kind": "initial_storage", "atom_ids": None},
        "case": {"seed": seed, "overrides": {}},
    }
    job = loads_job(json.dumps(document), base_dir=MODULE)
    if bit is None:
        return job
    overrides = {node.result: bit for node in job.dag.nodes if node.result is not None}
    return replace(
        job,
        measurement_backend=FakeBackend(seed, overrides),
        document={
            **job.document,
            "case": {
                "seed": seed,
                "overrides": {
                    str(reference.measurement_id): value
                    for reference, value in overrides.items()
                },
            },
        },
    )


def run_study(
    *,
    seed: int = 17,
    placement: str = "ids",
    transport: str = "strict",
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Compile each circuit/case on all profiles and report measured metrics.

    Args:
        seed: Shared FakeBackend seed.
        placement: The same existing placement kind for every row.
        transport: The same supported transport kind for every row.
        results_dir: Optional directory for validated self-contained v2 results.
    Returns:
        Comparable metrics and validation/replay evidence, with no assumed
        performance ordering between motion models.
    Raises:
        ValueError: A compiled row fails semantic validation or final replay.
    """
    rows = []
    if results_dir is not None:
        results_dir.mkdir(parents=True, exist_ok=True)
    for case, circuit, bit in CASES:
        for profile in PROFILES:
            job = study_job(
                profile,
                circuit,
                bit,
                seed=seed,
                placement=placement,
                transport=transport,
            )
            result = compile_job(job)
            validation = validate_result(
                result, entry_state=job.entry_state, device=job.device, dag=job.dag
            )
            matches = (
                replay(result.program, job.entry_state, job.device) == result.exit_state
            )
            if not validation["ok"] or not matches:
                raise ValueError(f"{profile}/{case}: {validation}; replay={matches}")
            path = None
            if results_dir is not None:
                destination = (results_dir / f"{case}-{profile}.result.json").resolve()
                saved = encode_result(
                    result,
                    entry_state=job.entry_state,
                    job=job.document,
                    dag=job.dag,
                    device=job.device,
                    inputs=job.inputs,
                    validation=validation,
                )
                destination.write_text(
                    json.dumps(saved, indent=2) + "\n", encoding="utf-8"
                )
                path = str(destination)
            aod = job.device.aod("transport")
            rows.append(
                {
                    "profile": profile,
                    "case": case,
                    "circuit": circuit,
                    "qubits": job.dag.num_qubits,
                    "motion": aod.motion,
                    "max_rows": aod.max_rows,
                    "max_columns": aod.max_columns,
                    "qasm_sha256": job.inputs["qasm_sha256"],
                    "metrics": collect_metrics(result),
                    "measurements": {
                        str(record.result.measurement_id): record.value
                        for record in result.measurements
                    },
                    "validation": validation,
                    "replay_matches_exit_state": matches,
                    "result": path,
                }
            )
    return {"seed": seed, "placement": placement, "transport": transport, "rows": rows}


def main(argv: Sequence[str] | None = None) -> None:
    """Print the comparison as JSON, optionally saving report and results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--placement", choices=("ids", "multipair"), default="ids")
    parser.add_argument(
        "--transport", choices=("strict", "sequential"), default="strict"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args(argv)
    report = run_study(
        seed=args.seed,
        placement=args.placement,
        transport=args.transport,
        results_dir=args.results_dir,
    )
    output = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
