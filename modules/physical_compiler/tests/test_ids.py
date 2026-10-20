"""Bounded iterative diving behavior independent of atom placement."""

from collections.abc import Mapping

import pytest

from nadit_physical_compiler.placement.ids import iterative_diving_search


def _search(
    tree: Mapping[str, tuple[str, ...]],
    costs: Mapping[str, float],
    goals: set[str],
    *,
    trials: int = 4,
    queue_capacity: int = 100,
) -> str:
    return iterative_diving_search(
        "root",
        expand=lambda node: tree.get(node, ()),
        is_goal=lambda node: node in goals,
        cost=costs.__getitem__,
        heuristic=lambda _: 0.0,
        trials=trials,
        queue_capacity=queue_capacity,
    )


def test_dead_ends_do_not_consume_completed_goal_trials() -> None:
    tree = {"root": ("dead", "a", "b"), "a": ("goal_a",), "b": ("goal_b",)}
    costs = {"root": 0, "dead": 0, "a": 1, "b": 2, "goal_a": 5, "goal_b": 3}

    assert _search(tree, costs, {"goal_a", "goal_b"}, trials=1) == "goal_a"
    assert _search(tree, costs, {"goal_a", "goal_b"}, trials=2) == "goal_b"


def test_best_child_is_dived_directly_before_better_queued_alternatives() -> None:
    tree = {"root": ("a", "b"), "a": ("goal_a",), "b": ("goal_b",)}
    costs = {"root": 0, "a": 1, "b": 2, "goal_a": 20, "goal_b": 3}

    assert _search(tree, costs, {"goal_a", "goal_b"}, trials=1) == "goal_a"
    assert _search(tree, costs, {"goal_a", "goal_b"}, trials=2) == "goal_b"


def test_initial_queue_capacity_includes_trial_budget() -> None:
    tree = {"root": ("dead_a", "dead_b", "goal")}
    costs = {"root": 0, "dead_a": 0, "dead_b": 1, "goal": 2}

    with pytest.raises(ValueError, match="goal"):
        _search(tree, costs, {"goal"}, trials=1, queue_capacity=0)
    assert _search(tree, costs, {"goal"}, trials=1, queue_capacity=1) == "goal"


def test_equal_priority_is_deterministic_and_keeps_first_goal() -> None:
    tree = {"root": ("a", "b", "c")}
    costs = {"root": 0, "a": 1, "b": 1, "c": 1}

    assert _search(tree, costs, {"a", "b", "c"}, trials=3, queue_capacity=0) == "a"
    assert _search(tree, costs, {"a", "b", "c"}, trials=3, queue_capacity=0) == "a"


def test_goal_heuristic_participates_in_reference_priority() -> None:
    assert (
        iterative_diving_search(
            "root",
            expand=lambda node: ("a", "b") if node == "root" else (),
            is_goal=lambda node: node != "root",
            cost=lambda node: {"root": 0, "a": 2, "b": 1}[node],
            heuristic=lambda node: {"root": 0, "a": 0, "b": 3}[node],
            trials=2,
        )
        == "a"
    )


def test_initial_goal_and_no_goal_tree() -> None:
    assert _search({}, {"root": 0}, {"root"}) == "root"
    with pytest.raises(ValueError, match="goal"):
        _search({}, {"root": 0}, set())


@pytest.mark.parametrize(("trials", "capacity"), [(0, 1), (-1, 1), (1, -1)])
def test_invalid_search_budgets_are_rejected(trials: int, capacity: int) -> None:
    with pytest.raises(ValueError):
        _search({}, {"root": 0}, {"root"}, trials=trials, queue_capacity=capacity)


def test_bounded_queue_keeps_best_alternatives_after_many_replacements() -> None:
    children = tuple(f"n{index}" for index in range(200, -1, -1))
    tree = {"root": children, "n0": ()}
    costs = {"root": 0, **{f"n{index}": float(index) for index in range(201)}}

    assert (
        _search(tree, costs, set(children) - {"n0"}, trials=1, queue_capacity=2) == "n1"
    )
