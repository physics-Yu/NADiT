"""One isolated compile/validate job; invoked only by the experiment runner."""

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from ..activity import _area
from ..compilers.contracts import InvalidProgramResult
from ..compilers.registry import identity as compiler_identity
from ..errors import SearchExhausted
from ..job import compile_job, loads_job
from ..serialization import encode_result
from ..support import UnsupportedJob
from ..validation import validate_result
from .journal import write_json


def execute(run: dict[str, Any]) -> dict[str, Any]:
    """Classify every admission/compile/validation outcome without losing errors."""
    started = time.perf_counter()
    record = {key: value for key, value in run.items() if key != "job"}
    record.update(
        status="invalid_job",
        reason=None,
        metrics={},
        cost=[],
        validation=None,
        compile={
            "wall_s": None,
            "peak_rss_mb": None,
            "search_nodes": None,
            "budget_hit": None,
        },
        hardware={},
        provenance={"job_hash": None, "result_path": None},
    )
    try:
        qasm = Path(run["job"]["circuit"]["qasm"])
        if hashlib.sha256(qasm.read_bytes()).hexdigest() != run["circuit_sha256"]:
            raise ValueError("Circuit bytes differ from the frozen suite.")
        job = loads_job(json.dumps(run["job"]), base_dir=qasm.parent)
        if job.dag.num_qubits != run["qubits"]:
            raise ValueError("Suite width differs from the loaded circuit.")
    except UnsupportedJob as error:
        record.update(status="unsupported", reason=error.document()["reasons"])
    except (ValueError, OSError, KeyError, TypeError) as error:
        record["reason"] = f"{type(error).__name__}: {error}"
    else:
        record["compiler"] = compiler_identity(job.document["strategy"])
        aod = job.device.aod(job.document["aod"])
        record["hardware"] = {
            "slm_sites": len(job.device.slm_sites),
            "gate_pairs": job.catalog.capacity if job.catalog else 0,
            "aod_capacity": {"rows": aod.max_rows, "columns": aod.max_columns},
            "zone_area_um2": _area(tuple(zone.bounds for zone in job.device.zones)),
        }
        compile_start = time.perf_counter()
        try:
            result = compile_job(job)
        except InvalidProgramResult as error:
            record.update(
                status="validation_failed", reason=str(error), validation=error.report
            )
        except SearchExhausted as error:
            record.update(status="search_exhausted", reason=str(error))
            record["compile"]["budget_hit"] = True
        except Exception as error:
            record.update(
                status="compile_error", reason=f"{type(error).__name__}: {error}"
            )
        else:
            record["compile"]["wall_s"] = time.perf_counter() - compile_start
            try:
                validation = validate_result(
                    result, entry_state=job.entry_state, device=job.device, dag=job.dag
                )
                document = encode_result(
                    result,
                    entry_state=job.entry_state,
                    job=job.document,
                    dag=job.dag,
                    device=job.device,
                    inputs=job.inputs,
                    validation=validation,
                )
            except Exception as error:
                record.update(
                    status="validation_failed",
                    reason=f"{type(error).__name__}: {error}",
                )
            else:
                record.update(
                    status="ok" if validation["ok"] else "validation_failed",
                    reason=validation["failure"],
                    metrics=document["metrics"],
                    validation=validation,
                    result=document,
                )
                record["provenance"]["job_hash"] = document["provenance"]["job_hash"]
        if record["compile"]["wall_s"] is None:
            record["compile"]["wall_s"] = time.perf_counter() - compile_start
    record["worker_wall_s"] = time.perf_counter() - started
    return record


def main() -> None:
    """Read one resolved run and atomically write its completion record."""
    run = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    write_json(Path(sys.argv[2]), execute(run))


if __name__ == "__main__":
    main()
