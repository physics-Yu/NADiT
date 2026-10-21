"""Cost CLI operations, preserving the compiled result content."""

import json
from pathlib import Path
from typing import Any

from . import evaluate
from .base import result_hash


def cost_result(
    path: Path, *, model: str, params_path: Path | None, into: Path | None
) -> dict[str, Any]:
    """Evaluate a saved result and optionally append a report atomically."""
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    params = (
        json.loads(params_path.read_text(encoding="utf-8-sig")) if params_path else {}
    )
    report = evaluate(document, model=model, params=params)
    if into is not None:
        target = (
            json.loads(into.read_text(encoding="utf-8-sig"))
            if into.exists()
            else document
        )
        if result_hash(target) != result_hash(document):
            raise ValueError("--into must refer to the evaluated execution content.")
        reports = target.setdefault("cost", [])
        identity = (
            report["model"],
            report["version"],
            report["params_hash"],
            report["result_hash"],
        )
        if not any(
            tuple(
                item.get(key)
                for key in ("model", "version", "params_hash", "result_hash")
            )
            == identity
            for item in reports
        ):
            reports.append(report)
        temporary = into.with_suffix(into.suffix + ".tmp")
        temporary.write_text(
            json.dumps(target, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        temporary.replace(into)
    return report
