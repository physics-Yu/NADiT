"""Failure-aware paired comparisons, statistics, Pareto sets, and viewer links."""

import csv
import json
import math
import shlex
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median, quantiles
from typing import Any

from ..provenance import canonical_hash
from .journal import read_records, write_json
from .sweep import get_path, set_path
from .viewers import viewer_record


def statistics(values: list[float]) -> dict[str, float | None]:
    """Return count, median and inclusive IQR; empty samples remain unavailable."""
    if not values:
        return {"count": 0, "median": None, "iqr": None}
    quartiles = (
        quantiles(values, n=4, method="inclusive")
        if len(values) > 1
        else [values[0]] * 3
    )
    return {
        "count": len(values),
        "median": median(values),
        "iqr": quartiles[2] - quartiles[0],
    }


def pareto(points: dict[str, tuple[float, ...]]) -> list[str]:
    """Return all nondominated minimization points, retaining equal alternatives."""
    return sorted(
        key
        for key, point in points.items()
        if not any(
            all(a <= b for a, b in zip(other, point, strict=True))
            and any(a < b for a, b in zip(other, point, strict=True))
            for name, other in points.items()
            if name != key
        )
    )


def _values(row: dict[str, Any], cost_id: str | None) -> dict[str, float | None]:
    report = next(
        (
            cost
            for cost in row.get("cost", [])
            if cost.get("id") == cost_id and cost.get("status") == "ok"
        ),
        None,
    )
    log_fidelity = report["metrics"]["log_fidelity"] if report else None
    metrics = row.get("metrics", {})
    return {
        "duration_us": metrics.get("duration_us"),
        "transfers": metrics.get("atom_load_count", 0)
        + metrics.get("atom_store_count", 0),
        "log_fidelity": log_fidelity,
        "quality_loss": -log_fidelity if log_fidelity is not None else None,
        "compile_wall_s": row.get("compile", {}).get("wall_s"),
        "slm_sites": row.get("hardware", {}).get("slm_sites"),
    }


def _baseline_candidates(
    row: dict[str, Any], rows: list[dict[str, Any]], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    selectors = baseline.get("architecture", {})

    def remainder(architecture: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(json.dumps(architecture))
        result.pop("name", None)
        for path in selectors:
            set_path(result, path, None)
        return result

    target = remainder(row["architecture"])
    candidates = []
    for other in rows:
        if (
            other["status"] != "ok"
            or other["circuit"] != row["circuit"]
            or other["case"] != row["case"]
        ):
            continue
        if other["strategy"] != baseline.get("strategy"):
            continue
        try:
            selected = all(
                get_path(other["architecture"], key) == value
                for key, value in selectors.items()
            )
        except (KeyError, IndexError):
            continue
        if selected and remainder(other["architecture"]) == target:
            candidates.append(other)
    return candidates


def summarize(
    directory: Path,
    *,
    cost_id: str | None = None,
    objectives: tuple[str, ...] = ("duration_us", "quality_loss", "slm_sites"),
    viewers: bool = False,
) -> dict[str, Any]:
    """Write summary JSON/CSV, preserving failed runs and unmatched baselines."""
    spec = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    rows = read_records(directory / "runs.jsonl")
    by_id = {row["run_id"]: row for row in rows}
    for update in read_records(directory / "costs.jsonl"):
        row = by_id[update["run_id"]]
        row["cost"] = [
            cost
            for cost in row.get("cost", [])
            if cost.get("id") != update["cost"]["id"]
        ] + [update["cost"]]
    if cost_id is None and spec.get("cost"):
        cost_id = canonical_hash(spec["cost"][0])
    values = {
        row["run_id"]: _values(row, cost_id) for row in rows if row["status"] == "ok"
    }
    if any(
        key
        not in (
            "duration_us",
            "quality_loss",
            "slm_sites",
            "transfers",
            "compile_wall_s",
        )
        for key in objectives
    ):
        raise ValueError("Unknown Pareto minimization objective.")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row["architecture_hash"] + ":" + row["strategy_hash"]
        if "compiler" in row:
            key += ":" + canonical_hash(row["compiler"])
        grouped[key].append(row)
    comparisons = []
    baseline = spec.get("baseline", {})
    if baseline:
        for row in rows:
            if row["status"] != "ok":
                continue
            candidates = _baseline_candidates(row, rows, baseline)
            comparison: dict[str, Any] = {
                "run_id": row["run_id"],
                "baseline_run_id": None,
                "ratios": {},
                "issues": [],
            }
            if len(candidates) != 1:
                comparison["issues"].append(
                    "missing_baseline" if not candidates else "ambiguous_baseline"
                )
            else:
                reference = candidates[0]
                comparison["baseline_run_id"] = reference["run_id"]
                for key in (
                    "duration_us",
                    "transfers",
                    "quality_loss",
                    "compile_wall_s",
                ):
                    a, b = values[row["run_id"]][key], values[reference["run_id"]][key]
                    if a is None or b is None or b <= 0 or a <= 0:
                        comparison["ratios"][key] = None
                        comparison["issues"].append(
                            f"{key}: unavailable or nonpositive value"
                        )
                    else:
                        comparison["ratios"][key] = a / b
            comparisons.append(comparison)
    compared = {item["run_id"]: item for item in comparisons}
    groups = []
    cohort_points: dict[str, dict[str, tuple[float, ...]]] = defaultdict(dict)
    for key, members in grouped.items():
        successful = [row for row in members if row["status"] == "ok"]
        stats = {
            metric: statistics(
                [
                    value
                    for row in successful
                    if (value := values[row["run_id"]][metric]) is not None
                ]
            )
            for metric in (
                "duration_us",
                "transfers",
                "log_fidelity",
                "quality_loss",
                "compile_wall_s",
                "slm_sites",
            )
        }
        ratios = {}
        for metric in ("duration_us", "transfers", "quality_loss", "compile_wall_s"):
            positive = [
                ratio
                for row in successful
                if (
                    ratio := compared.get(row["run_id"], {})
                    .get("ratios", {})
                    .get(metric)
                )
                is not None
            ]
            ratios[metric] = {
                "count": len(positive),
                "geometric_mean": math.exp(
                    sum(math.log(v) for v in positive) / len(positive)
                )
                if positive
                else None,
            }
        coverage = sorted(
            [row["circuit"], canonical_hash(row["case"])] for row in successful
        )
        cohort = canonical_hash({"coverage": coverage})
        vector = tuple(stats[metric]["median"] for metric in objectives)
        if (
            coverage
            and all(value is not None for value in vector)
            and all(stats[metric]["count"] == len(successful) for metric in objectives)
        ):
            cohort_points[cohort][key] = tuple(
                float(value) for value in vector if value is not None
            )
        groups.append(
            {
                "group_id": key,
                "architecture_hash": members[0]["architecture_hash"],
                "strategy": members[0]["strategy"],
                "compiler": members[0].get(
                    "compiler", {"kind": "search_smarter", "version": "legacy"}
                ),
                "statuses": dict(Counter(row["status"] for row in members)),
                "statistics": stats,
                "baseline_ratios": ratios,
                "coverage": coverage,
                "cohort": cohort,
            }
        )
    circuit_points: dict[str, dict[str, tuple[float, ...]]] = defaultdict(dict)
    for row in rows:
        if row["status"] == "ok":
            vector = tuple(values[row["run_id"]][metric] for metric in objectives)
            if all(value is not None for value in vector):
                key = row["circuit"] + ":" + canonical_hash(row["case"])
                circuit_points[key][row["run_id"]] = tuple(
                    float(value) for value in vector if value is not None
                )
    outlier_ids = {
        row["run_id"]
        for row in rows
        if row["status"] == "validation_failed"
        or row.get("compile", {}).get("budget_hit")
    }
    regressions = sorted(
        comparisons,
        key=lambda item: max(
            (ratio for ratio in item["ratios"].values() if ratio is not None), default=0
        ),
        reverse=True,
    )
    outlier_ids.update(
        item["run_id"]
        for item in regressions[:10]
        if any(r is not None and r > 1 for r in item["ratios"].values())
    )
    outliers = []
    for run_id in sorted(outlier_ids):
        row = by_id[run_id]
        outliers.append(
            {
                "run_id": run_id,
                "status": row["status"],
                "reason": row.get("reason"),
                **viewer_record(directory, row, generate=viewers),
                "reproduce_command": shlex.join(
                    [
                        "uv",
                        "run",
                        "--locked",
                        "nadit",
                        "compile",
                        str(
                            directory
                            / row["provenance"].get("job_path", f"jobs/{run_id}.json")
                        ),
                        "--validate",
                    ]
                ),
            }
        )
    report = {
        "cost_id": cost_id,
        "objectives": objectives,
        "statuses": dict(Counter(row["status"] for row in rows)),
        "groups": groups,
        "cost_statuses": dict(
            Counter(
                cost.get("status", "unknown")
                for row in rows
                for cost in row.get("cost", [])
            )
        ),
        "comparisons": comparisons,
        "pareto_per_case": {
            key: pareto(points) for key, points in circuit_points.items()
        },
        "pareto_by_coverage": {
            key: pareto(points) for key, points in cohort_points.items()
        },
        "outliers": outliers,
    }
    write_json(directory / "summary.json", report)
    with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        columns = [
            "group_id",
            "strategy",
            "compiler_kind",
            "compiler_version",
            "statuses",
            "duration_us",
            "transfers",
            "log_fidelity",
            "compile_wall_s",
        ]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for group in groups:
            writer.writerow(
                {
                    "group_id": group["group_id"],
                    "strategy": group["strategy"],
                    "compiler_kind": group["compiler"]["kind"],
                    "compiler_version": group["compiler"]["version"],
                    "statuses": json.dumps(group["statuses"], sort_keys=True),
                    **{key: group["statistics"][key]["median"] for key in columns[5:]},
                }
            )
    return report
