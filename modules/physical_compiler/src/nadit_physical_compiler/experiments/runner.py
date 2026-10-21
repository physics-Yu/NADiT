"""Bounded subprocess execution, content locks, and resumable JSONL results."""

import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..provenance import canonical_hash
from .costs import evaluate_models
from .journal import append, directory_lock, read_records, write_json
from .spec import Experiment, runs
from .version import check_version, code_version


def run_experiment(
    spec: Experiment,
    directory: Path,
    *,
    workers: int = 1,
    allow_dirty: bool = False,
    new_version: bool = False,
) -> dict[str, Any]:
    """Run each cell once with an enforced timeout; resume every completed status.

    Code changes stop the run and terminate active children. A new code version
    requires a separate version directory. Cost evaluation stays in the parent.
    """
    if workers < 1:
        raise ValueError("workers must be positive.")
    version = code_version(allow_dirty=allow_dirty)
    directory = directory.resolve()
    if new_version:
        directory = directory / "versions" / canonical_hash(version)[:16]
    with directory_lock(directory):
        expanded = runs(spec, version)
        manifest_path = directory / "manifest.json"
        spec_hash = canonical_hash(spec.document)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["code"] != version:
                raise ValueError("Experiment code version differs; use --new-version.")
            if manifest["spec_hash"] != spec_hash:
                raise ValueError(
                    "Experiment spec changed; use another directory "
                    "or experiment cost for new noise."
                )
        else:
            manifest = {
                "schema": "nadit.experiment-manifest/1",
                "code": version,
                "spec_hash": spec_hash,
                "suite_hash": spec.suite.sha256,
                "machine": platform.platform(),
                "cpu": platform.processor(),
                "started_at": datetime.now(UTC).isoformat(),
                "workers": workers,
                "run_count": len(expanded),
            }
            write_json(directory / "experiment.json", spec.document)
            write_json(manifest_path, manifest)
        journal = directory / "runs.jsonl"
        old = read_records(journal, repair=True)
        completed = {row["run_id"] for row in old}
        if len(completed) != len(old):
            raise ValueError("runs.jsonl contains duplicate completed run IDs.")
        pending = [run for run in expanded if run["run_id"] not in completed]
        _execute_pending(pending, spec, directory, version, workers)
        return status(directory)


def _execute_pending(
    pending: list[dict[str, Any]],
    spec: Experiment,
    directory: Path,
    version: dict[str, Any],
    workers: int,
) -> None:
    active: dict[
        str, tuple[subprocess.Popen[bytes], float, dict[str, Any], Path, Any]
    ] = {}
    costing: dict[
        Future[list[dict[str, Any]]], tuple[dict[str, Any], dict[str, Any], Path]
    ] = {}
    evaluator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nadit-cost")
    cursor = 0
    try:
        while cursor < len(pending) or active or costing:
            check_version(version)
            while (
                cursor < len(pending)
                and len(active) < workers
                and len(costing) < workers
            ):
                run = pending[cursor]
                cursor += 1
                job_path = directory / "jobs" / f"{run['run_id']}.json"
                write_json(job_path, run["job"])
                request = directory / ".work" / f"{run['run_id']}.input.json"
                output = request.with_suffix(".output.json")
                write_json(request, run)
                log = request.with_suffix(".log").open("wb")
                environment = {
                    **os.environ,
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                }
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "nadit_physical_compiler.experiments.worker",
                        str(request),
                        str(output),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                active[run["run_id"]] = (process, time.monotonic(), run, output, log)
            for run_id, (process, started, run, output, log) in tuple(active.items()):
                elapsed = time.monotonic() - started
                timed_out = (
                    process.poll() is None and elapsed >= spec.budget["timeout_s"]
                )
                if timed_out:
                    process.kill()
                    process.wait()
                if process.poll() is None:
                    continue
                log.close()
                check_version(version)
                if timed_out:
                    record = _failure(
                        run, "timeout", "Worker exceeded timeout_s", elapsed
                    )
                elif process.returncode or not output.exists():
                    record = _failure(
                        run,
                        "compile_error",
                        f"Worker exited {process.returncode}; see .work log",
                        elapsed,
                    )
                else:
                    record = json.loads(output.read_text(encoding="utf-8"))
                document = record.pop("result", None)
                if document is not None and record["status"] == "ok":
                    future = evaluator.submit(evaluate_models, document, spec.cost)
                    costing[future] = record, document, output
                else:
                    _finish(record, document, output, spec, directory, version)
                del active[run_id]
            for future, (record, document, output) in tuple(costing.items()):
                if not future.done():
                    continue
                try:
                    record["cost"] = future.result()
                except Exception as error:
                    record["cost"] = [{"status": "cost_error", "reason": str(error)}]
                _finish(record, document, output, spec, directory, version)
                del costing[future]
            if active or costing:
                time.sleep(0.05)
    finally:
        for process, _, _, _, log in active.values():
            if process.poll() is None:
                process.kill()
            process.wait()
            log.close()
        evaluator.shutdown(wait=True, cancel_futures=True)


def _finish(
    record: dict[str, Any],
    document: dict[str, Any] | None,
    output: Path,
    spec: Experiment,
    directory: Path,
    version: dict[str, Any],
) -> None:
    failed = record["status"] != "ok" or any(
        cost.get("status") != "ok" for cost in record["cost"]
    )
    keep = spec.store == "all" or (spec.store == "failures" and failed)
    run_id = record["run_id"]
    if document is not None and keep:
        relative = f"results/{run_id}.json"
        document["cost"] = record["cost"]
        write_json(directory / relative, document)
        record["provenance"]["result_path"] = relative
    record["provenance"]["job_path"] = f"jobs/{run_id}.json"
    check_version(version)
    append(directory / "runs.jsonl", record)
    output.unlink(missing_ok=True)
    (directory / ".work" / f"{run_id}.input.json").unlink(missing_ok=True)


def _failure(
    run: dict[str, Any], status_name: str, reason: str, elapsed: float
) -> dict[str, Any]:
    return {
        **{key: value for key, value in run.items() if key != "job"},
        "status": status_name,
        "reason": reason,
        "worker_wall_s": elapsed,
        "metrics": {},
        "hardware": {},
        "cost": [],
        "validation": None,
        "compile": {
            "wall_s": None,
            "peak_rss_mb": None,
            "search_nodes": None,
            "budget_hit": status_name == "timeout",
        },
        "provenance": {"job_hash": None, "result_path": None},
    }


def status(directory: Path) -> dict[str, Any]:
    """Report completed statuses, pending count, and a wall-time-based ETA."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    records = read_records(directory / "runs.jsonl")
    pending = manifest["run_count"] - len(records)
    average = (
        sum(row["worker_wall_s"] for row in records) / len(records) if records else None
    )
    return {
        "directory": str(directory),
        "planned": manifest["run_count"],
        "completed": len(records),
        "pending": pending,
        "statuses": dict(Counter(row["status"] for row in records)),
        "estimated_remaining_s": None
        if average is None
        else average * pending / manifest["workers"],
    }
