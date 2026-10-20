"""Run pinned paper inputs on original C++ and the local pipeline with timeouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from typing import Any

# Supports execution in the external paper environment without installing NADiT.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reference.benchmark import (  # noqa: E402
    PAPER_CASES,
    VARIANTS,
    compare_completed_runs,
    paper_settings,
    prepare_input,
    source_metrics,
)
from reference.pipeline_reference import fixture_input  # noqa: E402


def _worker(args: argparse.Namespace) -> None:
    if args.prepare_worker:
        name, qubits = args.prepare_worker
        summary = prepare_input(name, int(qubits), args.input)
        print(json.dumps(summary))
        return
    case = json.loads(args.input.read_text(encoding="utf-8"))
    case["config"] = paper_settings(args.variant)
    if args.engine == "local":
        from reference.pipeline_candidate import run_case

        result = run_case(case)
        output = {"metrics": result["metrics"], "trace": result}
    else:
        completed = subprocess.run(
            [str(args.binary)],
            input=fixture_input(case),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                f"Original C++ exit {completed.returncode}: {completed.stderr.strip()}"
            )
        trace = json.loads(completed.stdout)
        output = {"metrics": source_metrics(trace, case), "trace": trace}
    args.output.write_text(
        json.dumps(output, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _run_process(command: list[str], timeout: float) -> dict[str, Any]:
    start = perf_counter()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The source worker owns one child C++ process. Terminate the process
        # tree on Windows so a timed-out benchmark cannot keep consuming memory.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()
        stdout, stderr = process.communicate()
        return {
            "status": "timeout",
            "wall_s": perf_counter() - start,
            "timeout_s": timeout,
            "stderr": stderr[-4000:],
        }
    return {
        "status": "ok" if process.returncode == 0 else "error",
        "returncode": process.returncode,
        "wall_s": perf_counter() - start,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }


def main() -> None:
    """Save native inputs, execute requested ablations, and retain every status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--paper-python", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument(
        "--engines", nargs="+", choices=("source", "local"), default=["source", "local"]
    )
    parser.add_argument(
        "--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS)
    )
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--prepare-timeout", type=float, default=900)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run-only", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--prepare-worker", nargs=2)
    parser.add_argument("--engine", choices=("source", "local"))
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.prepare_worker or args.engine:
        _worker(args)
        return
    if args.output_dir is None:
        parser.error("--output-dir is required")
    if not args.run_only and args.paper_python is None:
        parser.error("--paper-python is required to prepare original inputs")
    if not args.prepare_only and "source" in args.engines and args.binary is None:
        parser.error("--binary is required for source runs")
    root: Path = args.output_dir
    inputs, outputs = root / "inputs", root / "runs"
    inputs.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).resolve())
    selected = [
        (benchmark, size)
        for benchmark, sizes in PAPER_CASES
        for size in sizes
        if not args.cases or f"{benchmark}_{size}" in args.cases
    ]
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {
        "source_commit": "cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d",
        "published_matrix": {name: list(sizes) for name, sizes in PAPER_CASES},
        "preparations": {},
        "runs": {},
        "timeout_s": args.timeout,
        "workers": args.workers,
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timeout_s"] = args.timeout
    manifest["workers"] = args.workers
    for benchmark, size in selected:
        name = f"{benchmark}_{size}"
        path = inputs / f"{name}.json"
        if path.exists():
            manifest["preparations"].setdefault(
                name,
                {
                    "status": "ok",
                    "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            )
        elif not args.run_only:
            record = _run_process(
                [
                    str(args.paper_python),
                    script,
                    "--prepare-worker",
                    benchmark,
                    str(size),
                    "--input",
                    str(path),
                ],
                args.prepare_timeout,
            )
            manifest["preparations"][name] = record
            print(f"prepare {name}: {record['status']}", flush=True)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
    if args.prepare_only:
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return
    jobs = [
        (f"{name}_{size}", engine, variant)
        for name, size in selected
        for engine in args.engines
        for variant in args.variants
        if (inputs / f"{name}_{size}.json").exists()
        and (args.rerun or f"{name}_{size}/{engine}/{variant}" not in manifest["runs"])
    ]

    def run(job: tuple[str, str, str]) -> tuple[str, dict[str, Any]]:
        name, engine, variant = job
        key = f"{name}/{engine}/{variant}"
        path = outputs / f"{name}_{engine}_{variant}.json"
        command = [
            sys.executable,
            script,
            "--engine",
            engine,
            "--variant",
            variant,
            "--input",
            str(inputs / f"{name}.json"),
            "--output",
            str(path),
        ]
        if engine == "source":
            command.extend(("--binary", str(args.binary)))
        record = _run_process(command, args.timeout)
        record["timeout_s"] = args.timeout
        if record["status"] == "ok":
            record["metrics"] = json.loads(path.read_text(encoding="utf-8"))["metrics"]
            record["trace_path"] = str(path.relative_to(root))
        print(f"{key}: {record['status']} ({record['wall_s']:.2f}s)", flush=True)
        return key, record

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for future in as_completed(futures):
            key, record = future.result()
            manifest["runs"][key] = record
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
    manifest["comparisons"] = compare_completed_runs(
        manifest,
        root,
        [f"{name}_{size}" for name, size in selected],
        args.variants,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
