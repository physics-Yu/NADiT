"""Bind cached source templates to actual resources and occupied intersections."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_transport_source_scope import _assert_complete, _case, _reversed_case

from nadit_physical_compiler.atom_ir import Move
from nadit_physical_compiler.device import AOD, Position, Region, SLMSite, Zone
from nadit_physical_compiler.transport import StagedStrictTransport, staged_plan
from nadit_physical_compiler.transport.occupancy import Occupancy
from nadit_physical_compiler.transport.staged_plan import StagingPlans
from nadit_physical_compiler.transport.staged_policy import staging_regions
from nadit_physical_compiler.transport.staging import generate_stages
from reference.cases import ReferenceCase


def _plans(case: ReferenceCase, aod: AOD | None = None) -> StagingPlans:
    return StagingPlans(
        case.entry,
        {atom.id: atom for atom in case.target.atoms},
        case.geometry,
        case.device.aod("transport") if aod is None else aod,
    )


def _occupied(case: ReferenceCase) -> Occupancy:
    return Occupancy({atom.id: atom.position for atom in case.entry.atoms})


def _diagonal(tmp_path: Path) -> ReferenceCase:
    return _case(
        tmp_path,
        "diagonal_template",
        ((0, 0, 0), (0, 1, 1)),
        ((1, 0, 0), (1, 1, 1)),
    )


def test_events_resource_binding_and_occupancy_reuse_one_ordered_construction(
    tmp_path: Path,
) -> None:
    case = _reversed_case(tmp_path, 3)
    plans = _plans(case)
    with patch.object(
        staged_plan, "generate_stages", wraps=generate_stages
    ) as generate:
        first = plans.events(case.moves)
        assert plans.fits(case.moves)
        assert plans.blockers(case.moves, _occupied(case)) == set()
        assert plans.events(case.moves) is first
        assert generate.call_count == 1

        reversed_moves = tuple(reversed(case.moves))
        second = plans.events(reversed_moves)
        assert plans.events(reversed_moves) is second
        assert plans.fits(reversed_moves)
        assert generate.call_count == 2
        assert generate.call_args_list[0].args[0] == case.moves
        assert generate.call_args_list[1].args[0] == reversed_moves


def test_resources_follow_constructed_lines_instead_of_the_whole_zone(
    tmp_path: Path,
) -> None:
    case = _reversed_case(tmp_path, 3)
    aod = replace(
        case.device.aod("transport"),
        workspace=Region(-6, 0, 16, 120),
        min_spacing=2,
        max_rows=1,
        max_columns=3,
    )

    assert _plans(case, aod).fits(case.moves)
    assert not _plans(case, replace(aod, workspace=Region(-5.9, 0, 16, 120))).fits(
        case.moves
    )
    assert not _plans(case, replace(aod, max_columns=2)).fits(case.moves)
    assert not _plans(case, replace(aod, min_spacing=2.1)).fits(case.moves)


def test_blockers_include_empty_carried_grid_intersections_and_fresh_occupancy(
    tmp_path: Path,
) -> None:
    case = _diagonal(tmp_path)
    plans = _plans(case)
    occupied = _occupied(case)
    ghost = Position(2.5, 5)
    assert all(
        ghost != position
        for event in plans.events(case.moves)
        for _, position in event.positions
    )

    occupied.add(99, ghost)
    assert plans.blockers(case.moves, occupied) == {99}
    occupied.remove(99)
    occupied.add(98, Position(3, 5))
    assert plans.blockers(case.moves, occupied) == set()


def test_empty_foreign_slm_site_does_not_block_a_source_template(
    tmp_path: Path,
) -> None:
    case = _diagonal(tmp_path)
    foreign = SLMSite("unused_foreign_site", Position(2.5, 5))
    device = replace(
        case.device,
        slm_sites=(*case.device.slm_sites, foreign),
        zones=(
            *case.device.zones,
            Zone("foreign", Region(2.5, 5, 2.5, 5), ("load", "store")),
        ),
    )
    case = replace(case, device=device)

    assert staging_regions(device, device.aod("transport"), case.geometry) == {
        "storage",
        "entanglement",
    }
    plans = _plans(case)
    assert plans.fits(case.moves)
    assert plans.blockers(case.moves, _occupied(case)) == set()
    fragment = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, device
    )
    assert _assert_complete(case, fragment) == 2


def test_single_line_motion_distance_is_independent_of_minimum_line_spacing(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, "single_line_short_shift", ((0, 0, 0),), ((1, 0, 0),))
    aod = replace(
        case.device.aod("transport"), min_spacing=10, max_rows=1, max_columns=1
    )
    case = replace(case, device=replace(case.device, aods=(aod,)))
    plans = _plans(case)
    assert plans.fits(case.moves)

    fragment = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    assert _assert_complete(case, fragment) == 1
    assert any(
        0 < action.duration_us < case.device.timing.move_duration(aod.min_spacing)
        for step in fragment.steps
        for action in step.actions
        if isinstance(action, Move)
    )
