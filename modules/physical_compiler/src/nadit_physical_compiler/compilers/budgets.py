"""Map neutral experiment budgets through compiler declarations."""

from copy import deepcopy
from typing import Any

from ..experiments.sweep import set_path
from .registry import registration


def apply_budget(
    spec: dict[str, Any], budget: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return configured options and explicit applied/not-applicable budget records."""
    registered = registration(spec)
    result = {
        "kind": registered.name,
        **deepcopy(registered.defaults),
        **deepcopy(spec),
    }
    records: dict[str, Any] = {}
    for name, value in budget.items():
        if name == "timeout_s":
            records[name] = {
                "value": value,
                "status": "applied",
                "path": "worker.timeout_s",
            }
            continue
        path = registered.budgets.get(name)
        applicable = path is not None and registered.budget_applicable(name, result)
        records[name] = {
            "value": value,
            "status": "applied" if applicable else "not_applicable",
            "path": path if applicable else None,
        }
        if applicable:
            assert path is not None
            set_path(result, path, value, field="budget")
    return result, records
