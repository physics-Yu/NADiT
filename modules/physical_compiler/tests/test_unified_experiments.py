"""Mixed compiler studies preserve each compiler's entry and budget policy."""

import json
from pathlib import Path

import pytest
from test_experiments import change, experiment_file
from test_job import MODULE

from nadit_physical_compiler.experiments.spec import load_spec, plan, runs
from nadit_physical_compiler.experiments.worker import execute


def test_mixed_compiler_entries_and_budget(tmp_path: Path) -> None:
    path = experiment_file(tmp_path)
    shared = json.loads((MODULE / "examples/architectures/nonzoned.json").read_text())
    change(path, lambda d: d["architectures"].append(shared))
    change(
        path,
        lambda d: d["strategies"].update(
            nonzoned={
                "strategy": {"kind": "nonzoned"},
                "entry": {
                    "kind": "enola_initial",
                    "iterations": 2,
                    "moves_per_iteration": 5,
                },
            }
        ),
    )
    change(path, lambda d: d["budget"].update(search_nodes=10000))
    spec = load_spec(path)
    expanded = runs(spec, {})
    zoned = next(
        r
        for r in expanded
        if r["strategy"] == "ids"
        and r["job"]["device"]["architecture"]["family"] == "zoned/v1"
    )
    shared_run = next(
        r
        for r in expanded
        if r["strategy"] == "nonzoned"
        and r["job"]["device"]["architecture"]["family"] == "shared/v1"
    )
    assert zoned["job"]["entry"]["kind"] == "initial_storage"
    assert shared_run["job"]["entry"]["kind"] == "enola_initial"
    assert shared_run["budgets"]["search_nodes"]["status"] == "not_applicable"
    assert zoned["job"]["strategy"]["placement"]["max_nodes"] == 10000
    before = execute(zoned)
    assert before["status"] == execute(shared_run)["status"] == "ok"
    change(path, lambda d: d["strategies"].pop("nonzoned"))
    solo = runs(load_spec(path), {})[0]
    assert solo["run_id"] == zoned["run_id"]
    assert execute(solo)["metrics"] == before["metrics"]


def test_global_entry_override_is_visible(tmp_path: Path) -> None:
    path = experiment_file(tmp_path)
    change(
        path,
        lambda d: d.update(
            entry={
                "kind": "explicit",
                "atoms": [[0, "storage_r0_c0", 0], [1, "storage_r0_c1", 1]],
            }
        ),
    )
    assert plan(load_spec(path), {})["entry_overrides"]


@pytest.mark.parametrize("method", ["ids", "astar"])
def test_search_exhaustion_is_a_budget_outcome(tmp_path: Path, method: str) -> None:
    path = experiment_file(tmp_path)
    change(path, lambda d: d["strategies"]["ids"].update(placement={"kind": method}))
    change(path, lambda d: d["budget"].update(search_nodes=1))
    record = execute(runs(load_spec(path), {})[0])
    assert record["status"] == "search_exhausted"
    assert record["compile"]["budget_hit"] is True


def test_ids_dead_end_is_structured() -> None:
    from nadit_physical_compiler.errors import SearchExhausted
    from nadit_physical_compiler.placement.ids import iterative_diving_search

    with pytest.raises(SearchExhausted, match="no completed goal"):
        iterative_diving_search(
            0,
            expand=lambda n: (),
            is_goal=lambda n: False,
            cost=lambda n: 0,
            heuristic=lambda n: 0,
        )


def test_overrides_and_unused_budget(tmp_path: Path) -> None:
    path = experiment_file(tmp_path)
    change(
        path,
        lambda d: d.update(strategy_overrides={"ids": {"placement.kind": "multipair"}}),
    )
    change(path, lambda d: d["budget"].update(search_nodes=3))
    run = runs(load_spec(path), {})[0]
    assert run["job"]["strategy"]["placement"]["kind"] == "multipair"
    assert run["budgets"]["search_nodes"]["status"] == "not_applicable"
    change(path, lambda d: d.update(strategy_overrides={"ids": {"missing.option": 1}}))
    with pytest.raises(ValueError, match="strategy_overrides.ids.missing.option"):
        load_spec(path)
