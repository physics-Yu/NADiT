"""Source A* queue semantics and shared placement evaluator integration."""

from collections.abc import Mapping
from dataclasses import replace

import pytest
from test_placement_scoring import _option, _rules

from nadit_physical_compiler.placement.astar import a_star_tree_search
from nadit_physical_compiler.placement.scoring import (
    PlacementConfig,
    PlacementJob,
    search_placement,
)


def _search(
    tree: Mapping[str, tuple[str, ...]],
    costs: Mapping[str, float],
    goals: set[str],
    *,
    max_nodes: int = 10_000_000,
) -> str:
    return a_star_tree_search(
        "root",
        expand=lambda node: tree.get(node, ()),
        is_goal=lambda node: node in goals,
        cost=costs.__getitem__,
        heuristic=lambda _: 0.0,
        max_nodes=max_nodes,
    )


def test_astar_selects_best_open_node_instead_of_diving() -> None:
    tree = {"root": ("a", "b"), "a": ("goal_a",), "b": ("goal_b",)}
    costs = {"root": 0, "a": 1, "b": 2, "goal_a": 20, "goal_b": 3}
    assert _search(tree, costs, {"goal_a", "goal_b"}) == "goal_b"


def test_open_set_limit_is_checked_after_expansion_including_goals() -> None:
    tree = {"root": ("a", "b")}
    costs = {"root": 0, "a": 1, "b": 2}
    with pytest.raises(ValueError, match="max_nodes"):
        _search(tree, costs, {"a", "b"}, max_nodes=2)
    assert _search(tree, costs, {"a", "b"}, max_nodes=3) == "a"


def test_max_nodes_counts_queue_size_not_total_visited() -> None:
    tree = {"root": ("a",), "a": ("b",), "b": ("goal",)}
    costs = {"root": 0, "a": 1, "b": 2, "goal": 3}
    assert _search(tree, costs, {"goal"}, max_nodes=2) == "goal"


def test_initial_goal_and_dead_ends() -> None:
    assert _search({}, {"root": 0}, {"root"}, max_nodes=1) == "root"
    with pytest.raises(ValueError, match="no completed goal"):
        _search({}, {"root": 0}, set())


@pytest.mark.parametrize("max_nodes", [0, -1])
def test_invalid_open_set_budget(max_nodes: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _search({}, {"root": 0}, {"root"}, max_nodes=max_nodes)


def test_ties_preserve_generation_order() -> None:
    assert (
        _search(
            {"root": ("c", "a", "b")},
            {"root": 0, "c": 1, "a": 1, "b": 1},
            {"a", "b", "c"},
        )
        == "c"
    )


def test_heuristic_participates_in_priority() -> None:
    result = a_star_tree_search(
        "root",
        expand=lambda node: ("a", "b") if node == "root" else (),
        is_goal=lambda node: node != "root",
        cost=lambda node: {"root": 0, "a": 2, "b": 1}[node],
        heuristic=lambda node: {"root": 0, "a": 0, "b": 3}[node],
    )
    assert result == "a"


def test_factory_and_paper_override_are_distinct() -> None:
    astar = PlacementConfig.for_method("astar")
    assert (
        astar.method,
        astar.max_nodes,
        astar.deepening_factor,
        astar.deepening_value,
        astar.lookahead_factor,
    ) == ("astar", 10_000_000, 0.8, 0.2, 0.2)
    assert PlacementConfig.for_method("ids") == PlacementConfig()
    paper = replace(astar, deepening_factor=0.9, deepening_value=8.0)
    assert paper.deepening_factor == 0.9 and paper.deepening_value == 8.0


def test_search_dispatch_uses_same_candidates_and_score() -> None:
    jobs = (
        PlacementJob(
            (
                _option(0, (0, 0), (0, 4), "a"),
                _option(0, (0, 0), (5, 4), "b"),
            )
        ),
        PlacementJob(
            (
                _option(1, (5, 0), (5, 4), "b"),
                _option(1, (5, 0), (0, 4), "a"),
            )
        ),
    )
    config = PlacementConfig(method="astar", deepening_factor=0)
    result = search_placement(jobs, _rules(), config)
    assert result == (jobs[0].options[0], jobs[1].options[0])
    assert search_placement((), _rules(), config) == ()
    with pytest.raises(ValueError, match="max_nodes"):
        search_placement(jobs, _rules(), replace(config, max_nodes=1))
