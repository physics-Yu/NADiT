"""Reference placement proxy formulas and Device-backed grouping."""

from math import sqrt

import pytest

from nadit_physical_compiler.device import AOD, Position, Region, SLMSite, Timing
from nadit_physical_compiler.placement.scoring import (
    PlacementConfig,
    PlacementJob,
    PlacementOption,
    _Node,
    _Search,
    search_placement,
)
from nadit_physical_compiler.state import COORDINATE_TOLERANCE
from nadit_physical_compiler.transport.rules import MoveRequest, StrictRules


def _rules(columns: int | None = None) -> StrictRules:
    return StrictRules(
        AOD("aod", Region(-100, -100, 100, 100), 1, None, columns),
        Timing(100, 200, 300),
    )


def _option(
    atom: int,
    start: tuple[float, float],
    end: tuple[float, float],
    site: str,
    lookahead: float = 0,
) -> PlacementOption:
    target = Position(*end)
    return PlacementOption(
        ((atom, SLMSite(site, target)),),
        (MoveRequest(atom, Position(*start), target),),
        lookahead,
    )


def test_config_and_option_properties() -> None:
    config = PlacementConfig()
    option = _option(0, (0, 0), (3, 4), "site")
    stay = PlacementOption(((0, SLMSite("gate", Position(0, 0))),), ())

    assert (
        config.deepening_factor,
        config.deepening_value,
        config.lookahead_factor,
        config.reuse_level,
        config.trials,
        config.queue_capacity,
    ) == (0.01, 0, 0.4, 5, 4, 100)
    assert option.distances == (5,)
    assert option.distance == 5
    assert option.site_ids == frozenset({"site"})
    assert stay.distances == () and stay.distance == 0


def test_cost_is_sqrt_of_each_group_maximum_plus_lookahead() -> None:
    jobs = (
        PlacementJob((_option(0, (0, 0), (3, 4), "a", 0.5),)),
        PlacementJob((_option(1, (5, 0), (8, 4), "b", 1.5),)),
        PlacementJob((_option(2, (10, 0), (12, 6), "c", 2),)),
    )
    search = _Search(jobs, _rules(), PlacementConfig(), "gate")
    node = _Node()
    for _ in jobs:
        node = next(iter(search.expand(node)))

    assert [len(group) for group in node.groups] == [2, 1]
    assert search.cost(node) == pytest.approx(sqrt(5) + sqrt(sqrt(40)) + 4)
    assert search.heuristic(node) == 0


def test_first_fit_respects_batch_capacity_without_using_transfer_time() -> None:
    jobs = tuple(
        PlacementJob((_option(index, (5 * index, 0), (5 * index, 10), str(index)),))
        for index in range(3)
    )
    search = _Search(jobs, _rules(columns=2), PlacementConfig(), "gate")
    node = _Node()
    for _ in jobs:
        node = next(iter(search.expand(node)))

    assert [len(group) for group in node.groups] == [2, 1]
    assert search.cost(node) == pytest.approx(2 * sqrt(10))


def test_heuristic_uses_first_free_option_and_mean_lookahead() -> None:
    first = _option(0, (0, 0), (0, 4), "taken", 2)
    blocked = _option(1, (5, 0), (0, 4), "taken", 1)
    available = _option(1, (5, 0), (5, 9), "free", 3)
    nearer_but_later = _option(1, (5, 0), (5, 5), "later", 5)
    jobs = (
        PlacementJob((first,)),
        PlacementJob((blocked, available, nearer_but_later)),
    )
    search = _Search(jobs, _rules(), PlacementConfig(deepening_factor=0), "gate")
    node = next(iter(search.expand(_Node())))

    assert search.heuristic(node) == pytest.approx(sqrt(9) - sqrt(4) + 3)
    assert len(tuple(search.expand(node))) == 2


def test_population_deviation_uses_unique_axis_ranks_and_remaining_job_count() -> None:
    jobs = (
        PlacementJob((_option(0, (0, 0), (0, 10), "a"),)),
        PlacementJob((_option(1, (10, 0), (20, 10), "b"),)),
        PlacementJob(
            (
                _option(2, (20, 0), (10, 10), "c"),
                _option(2, (20, 0), (30, 10), "d"),
            )
        ),
    )
    search = _Search(
        jobs,
        _rules(),
        PlacementConfig(deepening_factor=0.5, deepening_value=2),
        "gate",
    )
    node = _Node()
    node = next(iter(search.expand(node)))
    node = next(iter(search.expand(node)))
    # Source x ranks 0,1; target x ranks 0,2: population std of (0,1) is 0.5.
    nearest_unplaced = sqrt(200)
    already_placed = sqrt(200)
    expected = max(0, sqrt(nearest_unplaced) - sqrt(already_placed)) + 0.5 * (0.5 + 2)

    assert search.scale_factors == (1, 1)
    assert search.heuristic(node) == pytest.approx(expected)


def test_axis_ranks_share_the_transport_coordinate_tolerance() -> None:
    offset = COORDINATE_TOLERANCE / 2
    jobs = (
        PlacementJob((_option(0, (0, 0), (0, 10), "a"),)),
        PlacementJob((_option(1, (offset, 5), (0, 15), "b"),)),
        PlacementJob((_option(2, (0, 10), (0, 20), "c"),)),
    )
    search = _Search(jobs, _rules(), PlacementConfig(deepening_factor=1), "gate")
    node = next(iter(search.expand(_Node())))
    node = next(iter(search.expand(node)))

    assert len(node.groups) == 1
    assert search.source_ranks[1][0] == search.source_ranks[1][offset]
    assert search.scale_factors == (1, 1)
    assert search.heuristic(node) == pytest.approx(0)


def test_axis_scales_distinguish_gate_and_storage_and_handle_one_column() -> None:
    jobs = (
        PlacementJob(
            (
                _option(0, (0, 0), (0, 10), "a"),
                _option(0, (0, 0), (10, 10), "b"),
            )
        ),
        PlacementJob(
            (
                _option(1, (5, 0), (20, 10), "c"),
                _option(1, (5, 0), (10, 10), "b"),
            )
        ),
    )
    assert _Search(jobs, _rules(), PlacementConfig(), "gate").scale_factors == (1, 1)
    assert _Search(jobs, _rules(), PlacementConfig(), "storage").scale_factors == (
        1,
        1.5,
    )
    single = (PlacementJob((_option(0, (0, 0), (10, 10), "a"),)),)
    assert _Search(single, _rules(), PlacementConfig(), "storage").scale_factors == (
        1,
        1,
    )


def test_stay_consumes_its_site_without_creating_a_transport_group() -> None:
    site = SLMSite("gate", Position(0, 0))
    stay = PlacementOption(((0, site),), (), 0.25)
    conflict = _option(1, (5, 0), (0, 0), "gate")
    free = _option(1, (5, 0), (10, 10), "storage")
    jobs = (PlacementJob((stay,)), PlacementJob((conflict, free)))
    search = _Search(jobs, _rules(), PlacementConfig(), "storage")
    node = next(iter(search.expand(_Node())))

    assert node.groups == ()
    assert search.cost(node) == 0.25
    assert [child.chosen[-1] for child in search.expand(node)] == [free]
    assert search_placement(jobs, _rules(), PlacementConfig(), stage="storage") == (
        stay,
        free,
    )


def test_search_resolves_site_contention_and_rejects_unreachable_options() -> None:
    bad = _option(0, (0, 0), (200, 0), "far")
    a = _option(0, (0, 0), (0, 10), "shared")
    b = _option(0, (0, 0), (5, 10), "other")
    c = _option(1, (5, 0), (0, 10), "shared")

    assert search_placement(
        (PlacementJob((bad, a, b)), PlacementJob((c,))),
        _rules(),
        PlacementConfig(trials=4),
    ) == (b, c)
    assert search_placement((), _rules(), PlacementConfig()) == ()
    with pytest.raises(ValueError, match="goal"):
        search_placement((PlacementJob((bad,)),), _rules(), PlacementConfig())


def test_no_complete_site_assignment_reports_no_goal() -> None:
    jobs = (
        PlacementJob((_option(0, (0, 0), (0, 10), "same"),)),
        PlacementJob((_option(1, (5, 0), (0, 10), "same"),)),
    )
    with pytest.raises(ValueError, match="goal"):
        search_placement(jobs, _rules(), PlacementConfig())
