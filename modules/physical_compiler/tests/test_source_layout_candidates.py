"""Nearest-array candidates, source windows, and orientation score metadata."""

from dataclasses import replace
from math import hypot
from typing import Literal

import pytest

from nadit_physical_compiler.device import Position, SLMSite
from nadit_physical_compiler.placement.candidates import gate_jobs, storage_jobs
from nadit_physical_compiler.placement.geometry import PlacementGeometry, SLMArray
from nadit_physical_compiler.placement.scoring import PlacementConfig
from nadit_physical_compiler.placement.windows import candidate_indices
from nadit_physical_compiler.state import AtomState, SLMHolder


@pytest.mark.parametrize("ratio", [0.0, -1.0, float("inf"), float("nan")])
def test_window_ratio_must_define_a_finite_positive_shape(ratio: float) -> None:
    with pytest.raises(ValueError, match="aspect ratio"):
        PlacementConfig(window_ratio=ratio)


@pytest.mark.parametrize("share", [-1.0, float("inf"), float("nan")])
def test_window_share_must_define_a_finite_nonnegative_target(share: float) -> None:
    with pytest.raises(ValueError, match="Window share"):
        PlacementConfig(window_share=share)


def test_window_width_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="width"):
        PlacementConfig(window_min_width=-1)


def _array(
    name: str,
    kind: Literal["storage", "left", "right"],
    rows: int,
    columns: int,
    x: float,
    y: float,
    dx: float = 5,
    dy: float = 5,
) -> SLMArray:
    return SLMArray(
        name,
        kind,
        tuple(
            tuple(
                SLMSite(
                    f"{name}:{row}:{column}",
                    Position(x + column * dx, y + row * dy),
                )
                for column in range(columns)
            )
            for row in range(rows)
        ),
        Position(dx, dy),
    )


def _gate_arrays(
    name: str,
    rows: int,
    columns: int,
    x: float,
    y: float,
) -> tuple[SLMArray, SLMArray]:
    left = _array(name + "L", "left", rows, columns, x, y, 10, 10)
    right = _array(name + "R", "right", rows, columns, x + 2, y, 10, 10)
    return (
        replace(left, partner_sites=right.sites),
        replace(right, partner_sites=left.sites),
    )


def _atom(atom_id: int, site: SLMSite) -> AtomState:
    return AtomState(atom_id, site.position, SLMHolder(site.id))


def _pairs(geometry: PlacementGeometry) -> tuple[tuple[SLMSite, SLMSite], ...]:
    return tuple(
        array.gate_pair(row, column)
        for array, _ in geometry.entanglement_arrays
        for row in range(array.n_rows)
        for column in range(array.n_cols)
    )


def _sites(array: SLMArray) -> tuple[SLMSite, ...]:
    return tuple(site for row in array.sites for site in row)


def test_source_window_uses_inclusive_centered_extent() -> None:
    indices = candidate_indices(
        7,
        9,
        (3, 4),
        frozenset((r, c) for r in range(7) for c in range(9)),
        1,
        PlacementConfig(window_min_width=2),
        stage="gate",
    )

    assert indices == tuple((r, c) for r in range(2, 5) for c in range(3, 6))


@pytest.mark.parametrize("stage", ("gate", "storage"))
def test_expansion_adds_source_perimeters_without_duplicate_corners(
    stage: Literal["gate", "storage"],
) -> None:
    indices = candidate_indices(
        3,
        3,
        (1, 1),
        frozenset((r, c) for r in range(3) for c in range(3)),
        2,
        PlacementConfig(window_min_width=1, window_share=1),
        stage=stage,
    )

    assert indices == (
        (1, 1),
        (0, 0),
        (0, 1),
        (0, 2),
        (2, 0),
        (2, 1),
        (2, 2),
        (1, 0),
        (1, 2),
    )


def test_window_rounding_uses_cpp_half_up() -> None:
    indices = candidate_indices(
        7,
        7,
        (3, 3),
        frozenset((r, c) for r in range(7) for c in range(7)),
        1,
        PlacementConfig(window_min_width=5, window_ratio=0.5),
        stage="gate",
    )

    assert indices == tuple((r, c) for r in range(2, 5) for c in range(1, 6))


def test_occupied_center_expands_and_entire_array_exhaustion_terminates() -> None:
    indices = candidate_indices(
        1,
        5,
        (0, 2),
        frozenset(((0, 0),)),
        8,
        PlacementConfig(window_min_width=1),
        stage="gate",
    )

    assert indices == ((0, 0),)


def test_storage_stay_counts_towards_window_share() -> None:
    indices = candidate_indices(
        1,
        5,
        (0, 2),
        frozenset(((0, 0), (0, 4))),
        1,
        PlacementConfig(window_min_width=1),
        stage="storage",
        initial_options=1,
    )

    assert indices == ()


def test_disabling_window_enumerates_whole_selected_array() -> None:
    indices = candidate_indices(
        2,
        4,
        (0, 0),
        frozenset(((1, 3), (0, 2), (0, 1))),
        1,
        PlacementConfig(use_window=False),
        stage="gate",
    )

    assert indices == ((0, 1), (0, 2), (1, 3))


def test_gate_candidates_use_nearest_slm_before_free_candidate_distances() -> None:
    storage = _array("S", "storage", 1, 5, 0, 0)
    west = _gate_arrays("west", 1, 2, 0, 100)
    east = _gate_arrays("east", 1, 2, 200, 100)
    geometry = PlacementGeometry((storage,), (west, east))
    gates = ((_atom(0, storage.site(0, 0)), _atom(1, storage.site(0, 1))),)

    jobs = gate_jobs(
        gates,
        _pairs(geometry),
        config=PlacementConfig(use_window=False),
        geometry=geometry,
    )

    assert len(jobs[0].options) == 2
    assert all(
        site.id.startswith("west")
        for option in jobs[0].options
        for _, site in option.assignments
    )


def test_occupied_nearest_gate_pair_remains_window_center() -> None:
    storage = _array("S", "storage", 1, 7, 0, 0)
    gate = _gate_arrays("G", 1, 4, 0, 100)
    geometry = PlacementGeometry((storage,), (gate,))
    first, second = _atom(0, storage.site(0, 4)), _atom(1, storage.site(0, 5))
    available = tuple(
        pair for pair in _pairs(geometry) if pair != gate[0].gate_pair(0, 2)
    )

    job = gate_jobs(
        ((first, second),),
        available,
        geometry=geometry,
        config=PlacementConfig(window_min_width=1),
    )[0]

    assert {
        frozenset(site.id for _, site in option.assignments) for option in job.options
    } == {
        frozenset(site.id for site in gate[0].gate_pair(0, 1)),
        frozenset(site.id for site in gate[0].gate_pair(0, 3)),
    }


def test_reverse_orientation_retains_source_proxy_order_and_real_movements() -> None:
    storage = _array("S", "storage", 1, 3, 0, 0)
    gate = _gate_arrays("G", 1, 1, 0, 10)
    geometry = PlacementGeometry((storage,), (gate,))
    first, second = _atom(0, storage.site(0, 2)), _atom(1, storage.site(0, 0))

    option = gate_jobs(((first, second),), _pairs(geometry), geometry=geometry)[
        0
    ].options[0]

    assert option.assignments == (
        (first.id, gate[1].site(0, 0)),
        (second.id, gate[0].site(0, 0)),
    )
    assert option.distances == pytest.approx((10, hypot(8, 10)))
    assert tuple(
        hypot(move.target.x - move.source.x, move.target.y - move.source.y)
        for move in option.moves
    ) == pytest.approx((hypot(8, 10), 10))


def test_job_ties_and_gate_orientation_use_physical_qubit_identity() -> None:
    storage = _array("S", "storage", 1, 4, 0, 0)
    gate = _gate_arrays("G", 1, 1, 0, 10)
    geometry = PlacementGeometry((storage,), (gate,))
    first, second = _atom(9, storage.site(0, 2)), _atom(7, storage.site(0, 0))

    option = gate_jobs(
        ((second, first),),
        _pairs(geometry),
        geometry=geometry,
        qubit_ids={9: 0, 7: 1},
    )[0].options[0]

    assert tuple(atom_id for atom_id, _ in option.assignments) == (9, 7)


def test_nearest_gate_query_may_select_right_slm() -> None:
    storage = _array("S", "storage", 1, 3, 2, 0)
    gate = _gate_arrays("G", 1, 1, 0, 10)
    geometry = PlacementGeometry((storage,), (gate,))

    job = gate_jobs(
        ((_atom(0, storage.site(0, 0)), _atom(1, storage.site(0, 1))),),
        _pairs(geometry),
        geometry=geometry,
    )[0]

    assert {site.id for _, site in job.options[0].assignments} == {
        "GL:0:0",
        "GR:0:0",
    }


def test_storage_candidates_use_nearest_array_and_record_occupied_center() -> None:
    west = _array("west", "storage", 1, 5, 0, 0)
    east = _array("east", "storage", 1, 5, 200, 0)
    gate = _gate_arrays("G", 1, 1, 10, 100)
    geometry = PlacementGeometry((west, east), (gate,))
    atom = _atom(0, gate[0].site(0, 0))
    free = tuple(
        site for site in (*_sites(west), *_sites(east)) if site.id != "west:0:2"
    )

    job = storage_jobs(
        (atom,),
        free,
        geometry=geometry,
        config=PlacementConfig(window_min_width=1),
    )[0]

    assert job.nearest_site == west.site(0, 2)
    assert {option.assignments[0][1].id for option in job.options} == {
        "west:0:1",
        "west:0:3",
    }


def test_storage_stay_can_satisfy_initial_window_without_free_sites() -> None:
    storage = _array("S", "storage", 1, 3, 0, 0)
    gate = _gate_arrays("G", 1, 1, 5, 100)
    geometry = PlacementGeometry((storage,), (gate,))
    atom = _atom(3, gate[0].site(0, 0))

    job = storage_jobs(
        (atom,),
        (storage.site(0, 0),),
        reuse_atoms=frozenset((3,)),
        geometry=geometry,
        config=PlacementConfig(window_min_width=1),
    )[0]

    assert len(job.options) == 1
    assert job.options[0].moves == ()
    assert job.nearest_site == storage.site(0, 1)


def test_no_free_pair_in_nearest_slm_reports_the_source_search_scope() -> None:
    storage = _array("S", "storage", 1, 3, 0, 0)
    west = _gate_arrays("west", 1, 1, 0, 100)
    east = _gate_arrays("east", 1, 1, 200, 100)
    geometry = PlacementGeometry((storage,), (west, east))

    with pytest.raises(ValueError, match="nearest SLM"):
        gate_jobs(
            ((_atom(0, storage.site(0, 0)), _atom(1, storage.site(0, 1))),),
            (east[0].gate_pair(0, 0),),
            geometry=geometry,
        )
