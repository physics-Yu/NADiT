"""Candidate construction follows the pinned full-candidate placement policy."""

from math import hypot, sqrt

import pytest

from nadit_physical_compiler.device import Position, SLMSite
from nadit_physical_compiler.placement.candidates import gate_jobs, storage_jobs
from nadit_physical_compiler.placement.scoring import PlacementConfig
from nadit_physical_compiler.state import AtomState, SLMHolder


def _atom(atom_id: int, x: float, y: float) -> AtomState:
    return AtomState(atom_id, Position(x, y), SLMHolder(f"source{atom_id}"))


def _pair(prefix: str, x: float, y: float) -> tuple[SLMSite, SLMSite]:
    return (
        SLMSite(prefix + "a", Position(x, y)),
        SLMSite(prefix + "b", Position(x + 2, y)),
    )


def test_one_orientation_per_pair_minimizes_sum_of_distances() -> None:
    pair = _pair("g", 0, 10)
    job = gate_jobs(((_atom(7, 5, 0), _atom(3, 0, 0)),), (pair,))[0]

    assert len(job.options) == 1
    assert job.options[0].assignments == ((7, pair[1]), (3, pair[0]))
    assert tuple(move.atom_id for move in job.options[0].moves) == (7, 3)


def test_equal_orientation_cost_retains_original_pair_order() -> None:
    pair = _pair("g", 0, 0)
    option = gate_jobs(((_atom(7, 1, -1), _atom(3, 1, 1)),), (pair,))[0].options[0]

    assert option.assignments == ((7, pair[0]), (3, pair[1]))


def test_gate_options_use_lexicographic_distance_arrays() -> None:
    pair_a, pair_b = _pair("a", 0, 1), _pair("b", 50, 0)
    options = gate_jobs(((_atom(7, 0, 0), _atom(3, 100, 0)),), (pair_b, pair_a))[
        0
    ].options

    assert options[0].assignments[0][1] == pair_a[0]
    assert options[0].distance > options[1].distance
    assert options[0].distances < options[1].distances


def test_gate_jobs_start_with_farthest_nearest_pair() -> None:
    jobs = gate_jobs(
        (
            (_atom(1, 0, 0), _atom(2, 2, 0)),
            (_atom(3, 0, -100), _atom(4, 2, -100)),
        ),
        (_pair("g", 0, 10),),
    )

    assert [job.options[0].assignments[0][0] for job in jobs] == [3, 1]


def test_gate_lookahead_uses_only_a_matched_reuse_endpoint() -> None:
    pair = _pair("g", 0, 10)
    config = PlacementConfig(lookahead_factor=0.25)
    gates = ((_atom(7, 0, 0), _atom(3, 2, 0)),)
    partner = {7: Position(10, 10), 3: Position(50, 10)}
    without_hint = gate_jobs(gates, (pair,), partner_positions=partner, config=config)
    with_hint = gate_jobs(
        gates,
        (pair,),
        reuse_atoms=frozenset((7,)),
        partner_positions=partner,
        config=config,
    )

    assert without_hint[0].options[0].lookahead_cost == 0
    assert with_hint[0].options[0].lookahead_cost == pytest.approx(0.25 * sqrt(10))


def test_storage_jobs_start_farthest_then_follow_distance_from_first_atom() -> None:
    atoms = (
        _atom(1, 0, 1),
        _atom(2, 10, 100),
        _atom(3, 5, 5),
        _atom(4, 20, 100),
    )
    sites = (SLMSite("left", Position(0, 0)), SLMSite("right", Position(30, 0)))

    jobs = storage_jobs(atoms, sites)

    assert [job.options[0].assignments[0][0] for job in jobs] == [2, 4, 3, 1]


def test_storage_and_stay_options_use_different_partner_costs() -> None:
    atom = _atom(7, 0, 100)
    site = SLMSite("s", Position(0, 0))
    job = storage_jobs(
        (atom,),
        (site,),
        reuse_atoms=frozenset((7,)),
        partner_positions={7: Position(10, 0)},
        config=PlacementConfig(lookahead_factor=0.4, reuse_level=5),
    )[0]
    stay, store = job.options

    assert stay.moves == ()
    assert stay.assignments == ((7, SLMSite("source7", atom.position)),)
    assert stay.lookahead_cost == pytest.approx(sqrt(hypot(10, 100)) - 5)
    assert store.lookahead_cost == pytest.approx(0.4 * sqrt(10))


def test_stay_does_not_need_a_storage_candidate() -> None:
    job = storage_jobs(
        (_atom(7, 0, 100),),
        (),
        reuse_atoms=frozenset((7,)),
        partner_positions={7: Position(2, 100)},
    )[0]

    assert len(job.options) == 1
    assert job.options[0].moves == ()
    assert job.options[0].lookahead_cost == 0


def test_missing_storage_and_stay_options_report_no_candidate() -> None:
    with pytest.raises(ValueError, match="storage"):
        storage_jobs((_atom(7, 0, 100),), ())


def test_empty_candidate_jobs_need_no_search() -> None:
    assert gate_jobs((), ()) == ()
    assert storage_jobs((), ()) == ()
