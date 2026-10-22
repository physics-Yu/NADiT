"""Resolve an experiment into immutable job inputs and content-based run IDs."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..compilers.budgets import apply_budget
from ..compilers.registry import entry_for
from ..compilers.registry import identity as compiler_identity
from ..job import loads_job
from ..job_fields import array_at, keys_at, object_at
from ..provenance import canonical_hash
from ..support import UnsupportedJob
from .suite import Suite, load_suite
from .sweep import expand, set_path


@dataclass(frozen=True, slots=True)
class Experiment:
    """Resolved immutable inputs; referenced JSON files are snapshotted."""

    name: str
    document: dict[str, Any]
    suite: Suite
    architectures: tuple[dict[str, Any], ...]
    strategies: dict[str, dict[str, Any]]
    cases: tuple[dict[str, Any], ...]
    cost: tuple[dict[str, Any], ...]
    budget: dict[str, Any]
    entry: dict[str, Any] | None
    store: str


def _json(value: Any, base: Path) -> dict[str, Any]:
    return object_at(
        json.loads((base / value).read_text(encoding="utf-8"))
        if isinstance(value, str)
        else value,
        "configuration",
    )


def load_spec(path: str | Path) -> Experiment:
    """Read and resolve a nadit.experiment/1 spec without compiling circuits."""
    path = Path(path).resolve()
    raw = _json(path.name, path.parent)
    keys_at(
        raw,
        {
            "schema",
            "name",
            "suite",
            "architectures",
            "strategies",
            "cases",
            "cost",
            "budget",
            "strategy_overrides",
            "entry",
            "validate",
            "store",
            "baseline",
        },
        "experiment",
    )
    if raw.get("schema") != "nadit.experiment/1" or raw.get("validate") != "required":
        raise ValueError(
            "Experiment requires nadit.experiment/1 and validate=required."
        )
    store = raw.get("store", "failures")
    if store not in ("none", "failures", "all"):
        raise ValueError("store: expected none, failures, or all.")
    suite = load_suite(raw["suite"], path.parent)
    architectures = []
    for item in array_at(raw["architectures"], "architectures"):
        keys_at(
            item,
            {"family", "base", "params", "sweep", "zip", "exclude", "aod"},
            "architecture",
        )
        params = _json(item.get("base", item.get("params", {})), path.parent)
        if "params" in params and "family" in params:
            params = params["params"]
        for values in expand(
            params, item.get("sweep", {}), item.get("zip", []), item.get("exclude", [])
        ):
            architectures.append(
                {
                    "family": item["family"],
                    "params": values,
                    "aod": item.get(
                        "aod", values.get("aod", {}).get("id", "transport")
                    ),
                }
            )
    strategies = {
        name: _json(value, path.parent) for name, value in raw["strategies"].items()
    }
    overrides_by_strategy = object_at(
        raw.get("strategy_overrides", {}), "strategy_overrides"
    )
    for name, overrides in overrides_by_strategy.items():
        if name not in strategies:
            raise ValueError(f"strategy_overrides.{name}: unknown strategy label")
        target = strategies[name].get("strategy", strategies[name])
        for key, value in object_at(overrides, f"strategy_overrides.{name}").items():
            set_path(target, key, value, field=f"strategy_overrides.{name}")
    cases = object_at(raw.get("cases", {"seeds": [0]}), "cases")
    keys_at(cases, {"seeds", "overrides"}, "cases")
    seeds = cases.get("seeds", [0])
    if not seeds or any(type(seed) is not int for seed in seeds):
        raise ValueError("cases.seeds: expected nonempty integer seeds.")
    overrides = cases.get("overrides", [{}])
    if isinstance(overrides, dict):
        overrides = [overrides]
    case_values = tuple(
        {"seed": seed, "overrides": override}
        for seed in seeds
        for override in overrides
    )
    costs = tuple(
        {
            "model": item.get("model", "additive"),
            "params": _json(item.get("params", {}), path.parent),
        }
        for item in raw.get("cost", [{"model": "additive", "params": {}}])
    )
    budget = {"timeout_s": 900, **raw.get("budget", {})}
    keys_at(budget, {"timeout_s", "search_nodes"}, "budget")
    if "search_nodes" in budget and (
        type(budget["search_nodes"]) is not int or budget["search_nodes"] < 1
    ):
        raise ValueError("budget.search_nodes: expected a positive integer.")
    if type(budget["timeout_s"]) not in (int, float) or budget["timeout_s"] <= 0:
        raise ValueError("budget.timeout_s: expected a positive duration.")
    document = {
        **raw,
        "suite": {"name": suite.name, "sha256": suite.sha256},
        "architectures": architectures,
        "strategies": strategies,
        "cases": list(case_values),
        "cost": list(costs),
        "budget": budget,
        "entry": raw.get("entry"),
        "store": store,
    }
    return Experiment(
        raw["name"],
        document,
        suite,
        tuple(architectures),
        strategies,
        case_values,
        costs,
        budget,
        document["entry"],
        store,
    )


def runs(spec: Experiment, version: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand all axes with stable IDs; cost parameters never enter compile identity."""
    result = []
    seen: set[str] = set()
    for architecture in spec.architectures:
        for name, original in spec.strategies.items():
            raw_strategy = original.get("strategy", original)
            strategy, budgets = apply_budget(raw_strategy, spec.budget)
            entry = entry_for(
                strategy,
                spec.entry if spec.entry is not None else original.get("entry"),
            )
            compiler = compiler_identity(strategy)
            default_entry = entry_for(strategy, None)
            for circuit in spec.suite.circuits:
                for case in spec.cases:
                    identity = {
                        "architecture": architecture,
                        "strategy": strategy,
                        "compiler": compiler,
                        "circuit_sha256": circuit.sha256,
                        "gate_set": spec.suite.gate_set,
                        "case": case,
                        "budget": spec.budget,
                        "code": version,
                        "entry": entry,
                    }
                    run_id = canonical_hash(identity)
                    if run_id in seen:
                        continue
                    seen.add(run_id)
                    job = {
                        "schema": "nadit.job/1",
                        "circuit": {
                            "qasm": str(circuit.path),
                            "gate_set": list(spec.suite.gate_set),
                        },
                        "device": {
                            "architecture": {
                                "family": architecture["family"],
                                "params": architecture["params"],
                            }
                        },
                        "aod": architecture["aod"],
                        "strategy": strategy,
                        "entry": entry,
                        "case": case,
                    }
                    result.append(
                        {
                            "run_id": run_id,
                            "compiler": compiler,
                            "budgets": budgets,
                            "entry_override": {
                                "default": default_entry,
                                "selected": entry,
                            }
                            if spec.entry is not None and entry != default_entry
                            else None,
                            "architecture": architecture["params"],
                            "architecture_hash": canonical_hash(architecture),
                            "strategy": name,
                            "strategy_hash": canonical_hash(strategy),
                            "circuit": circuit.id,
                            "circuit_sha256": circuit.sha256,
                            "qubits": circuit.qubits,
                            "case": case,
                            "job": job,
                        }
                    )
    return result


def plan(spec: Experiment, version: dict[str, Any]) -> dict[str, Any]:
    """Perform normal job admission for every cell, without compiling or sampling."""
    expanded = runs(spec, version)
    rejected = []
    for run in expanded:
        try:
            job = loads_job(json.dumps(run["job"]), base_dir=Path.cwd())
            if job.dag.num_qubits != run["qubits"]:
                raise ValueError("suite qubit count disagrees with QASM")
        except UnsupportedJob as error:
            rejected.append({"run_id": run["run_id"], **error.document()})
        except (ValueError, OSError, KeyError, TypeError) as error:
            rejected.append(
                {"run_id": run["run_id"], "status": "invalid_job", "reason": str(error)}
            )
    return {
        "name": spec.name,
        "runs": len(expanded),
        "axes": {
            "architectures": len(spec.architectures),
            "strategies": len(spec.strategies),
            "circuits": len(spec.suite.circuits),
            "cases": len(spec.cases),
        },
        "suite_hash": spec.suite.sha256,
        "code": version,
        "rejected": rejected,
        "entry_overrides": [
            {"run_id": run["run_id"], **run["entry_override"]}
            for run in expanded
            if run["entry_override"]
        ],
    }
