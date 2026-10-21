"""Parent-side cost evaluation and post-hoc noise reevaluation journals."""

import json
from pathlib import Path
from typing import Any

from ..cost import model_for, prepare
from ..provenance import canonical_hash
from .journal import append, directory_lock, read_records


def evaluate_models(
    document: dict[str, Any], costs: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    """Share one validated activity across models, recording model failures."""
    if not costs:
        return []
    activity = prepare(document)
    result = []
    for config in costs:
        identity = canonical_hash(config)
        try:
            report = model_for(config["model"]).evaluate(
                activity, document, config["params"]
            )
        except Exception as error:
            result.append(
                {
                    "id": identity,
                    "status": "cost_error",
                    "model": config["model"],
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
        else:
            result.append({"id": identity, "status": "ok", **report})
    return result


def reevaluate(directory: Path, costs: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Append noise evaluations for retained valid results without launching workers."""
    from .version import code_version

    with directory_lock(directory):
        journal = directory / "costs.jsonl"
        existing = {
            (row["run_id"], row["cost"]["id"])
            for row in read_records(journal, repair=True)
        }
        evaluated = 0
        unavailable = []
        for row in read_records(directory / "runs.jsonl"):
            if row["status"] != "ok":
                continue
            relative = row["provenance"]["result_path"]
            if relative is None:
                unavailable.append(row["run_id"])
                continue
            pending = tuple(
                config
                for config in costs
                if (row["run_id"], canonical_hash(config)) not in existing
            )
            if not pending:
                continue
            document = json.loads((directory / relative).read_text(encoding="utf-8"))
            for cost in evaluate_models(document, pending):
                append(
                    journal,
                    {
                        "run_id": row["run_id"],
                        "cost": cost,
                        "evaluation_code": code_version(allow_dirty=True),
                    },
                )
                evaluated += 1
        return {"evaluated": evaluated, "unavailable_results": unavailable}
