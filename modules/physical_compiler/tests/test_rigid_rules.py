"""Rigid displacement groups share capacity, frame, and timing calculations."""

from dataclasses import replace
from itertools import combinations
from typing import Literal

import pytest

from nadit_physical_compiler.atom_ir import AtomProgram, Load, Move, Store
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.state import (
    COORDINATE_TOLERANCE,
    AtomLayout,
    AtomState,
    SLMHolder,
)
from nadit_physical_compiler.transport.rules import (
    MoveRequest,
    RigidRules,
    StrictRules,
    TransportRules,
    build_conflict_map,
    rules_for,
)
from nadit_physical_compiler.transport.strict import StrictTransport


def _rules(*, rows: int | None = None, columns: int | None = None) -> RigidRules:
    return RigidRules(
        AOD("a", Region(-100, -100, 100, 100), 1, rows, columns, motion="rigid"),
        Timing(3, 4, 2),
    )


def _move(
    atom: int, source: tuple[float, float], target: tuple[float, float]
) -> MoveRequest:
    return MoveRequest(atom, Position(*source), Position(*target))


@pytest.mark.parametrize(
    ("source", "target", "compatible"),
    [
        ((5, 0), (15, 20), True),
        ((0, 5), (10, 25), True),
        ((5, 5), (15, 25), True),
        ((5, 0), (12, 20), False),
        ((0, 5), (10, 30), False),
        ((5, 5), (12, 30), False),
        ((0.5, 0), (10.5, 20), False),
        ((0, 0.5), (10, 20.5), False),
    ],
)
def test_rigid_compatibility_requires_equal_displacement_and_spacing(
    source: tuple[float, float], target: tuple[float, float], compatible: bool
) -> None:
    first = _move(7, (0, 0), (10, 20))
    second = _move(11, source, target)
    rules = _rules()

    assert rules.compatible(first, second) is compatible
    assert rules.compatible(second, first) is compatible


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize(("offset", "compatible"), [(0.5, True), (2.0, False)])
def test_displacement_comparison_uses_absolute_coordinate_tolerance(
    axis: str, offset: float, compatible: bool
) -> None:
    delta = offset * COORDINATE_TOLERANCE
    target = (15 + delta, 25) if axis == "x" else (15, 25 + delta)
    first = _move(0, (0, 0), (10, 20))
    second = _move(1, (5, 5), target)

    assert _rules().compatible(first, second) is compatible


def test_equal_displacement_tolerance_does_not_relax_axis_sharing() -> None:
    tolerance = COORDINATE_TOLERANCE
    first = _move(0, (0, 0), (10, 20))
    # Each displacement is close, but one source column would split in two.
    second = _move(1, (0.8 * tolerance, 5), (10 + 1.5 * tolerance, 25))

    assert not _rules().compatible(first, second)


@pytest.mark.parametrize("axis", ["row", "column"])
def test_capacity_is_a_group_constraint_after_rigid_compatibility(axis: str) -> None:
    moves = tuple(
        _move(
            index,
            (0, 5 * index) if axis == "row" else (5 * index, 0),
            (10, 20 + 5 * index) if axis == "row" else (10 + 5 * index, 20),
        )
        for index in range(3)
    )
    rules = _rules(
        rows=2 if axis == "row" else None, columns=2 if axis == "column" else None
    )

    assert all(
        rules.compatible(first, second) for first, second in combinations(moves, 2)
    )
    assert build_conflict_map(moves, rules) == {
        index: frozenset() for index in range(3)
    }
    assert rules.can_fit(moves[:2])
    assert not rules.can_fit(moves)


def test_frames_and_timing_reuse_the_existing_geometry_calculations() -> None:
    moves = (
        _move(7, (5, 5), (15, 25)),
        _move(11, (0, 0), (10, 20)),
        _move(19, (5, 0), (15, 20)),
    )
    rigid = _rules()
    flexible = StrictRules(replace(rigid.aod, motion="flexible"), rigid.timing)

    assert rigid.frames(moves) == flexible.frames(moves)
    assert rigid.duration_us(moves) == flexible.duration_us(moves)
    assert rigid.duration_us(()) == 0
    assert rigid.can_fit(())
    source, target, bindings = rigid.frames(moves)
    assert bindings == ((7, 1, 1), (11, 0, 0), (19, 0, 1))
    assert [
        after.coordinate - before.coordinate
        for before, after in zip(source.rows, target.rows, strict=True)
    ] == [20, 20]
    assert [
        after.coordinate - before.coordinate
        for before, after in zip(source.columns, target.columns, strict=True)
    ] == [10, 10]


def test_conflict_map_uses_displacement_even_when_flexible_axes_are_compatible() -> (
    None
):
    moves = (
        _move(7, (0, 0), (10, 20)),
        _move(11, (5, 0), (12, 20)),
        _move(19, (10, 0), (20, 20)),
    )
    rules = _rules()

    assert build_conflict_map(moves, rules) == {
        7: frozenset({11}),
        11: frozenset({7, 19}),
        19: frozenset({11}),
    }
    assert build_conflict_map(moves, StrictRules(rules.aod, rules.timing)) == {
        atom: frozenset() for atom in (7, 11, 19)
    }


@pytest.mark.parametrize(
    ("motion", "kind"), [("flexible", StrictRules), ("rigid", RigidRules)]
)
def test_rules_factory_selects_motion_and_keeps_device_objects(
    motion: Literal["flexible", "rigid"], kind: type[StrictRules]
) -> None:
    aod = replace(_rules().aod, motion=motion)
    timing = Timing(1, 2, 3)
    rules: TransportRules = rules_for(aod, timing)

    assert type(rules) is kind
    assert rules.aod is aod
    assert rules.timing is timing


def _case(
    sources: tuple[tuple[float, float], ...],
    targets: tuple[tuple[float, float], ...],
    *,
    columns: int | None = None,
    stationary: tuple[tuple[float, float], ...] = (),
) -> tuple[Device, AtomLayout, AtomLayout]:
    source_sites = tuple(
        SLMSite(f"s{i}", Position(*point)) for i, point in enumerate(sources)
    )
    target_sites = tuple(
        SLMSite(f"t{i}", Position(*point)) for i, point in enumerate(targets)
    )
    static_sites = tuple(
        SLMSite(f"p{i}", Position(*point)) for i, point in enumerate(stationary)
    )
    rules = _rules(columns=columns)
    device = Device(
        "rigid",
        (),
        source_sites + target_sites + static_sites,
        (rules.aod,),
        rules.timing,
    )
    spectators = tuple(
        AtomState(100 + i, site.position, SLMHolder(site.id))
        for i, site in enumerate(static_sites)
    )
    binding = tuple(range(len(sources)))
    entry = AtomLayout(
        tuple(
            AtomState(i, site.position, SLMHolder(site.id))
            for i, site in enumerate(source_sites)
        )
        + spectators,
        qubit_to_atom=binding,
    )
    target = AtomLayout(
        tuple(
            AtomState(i, site.position, SLMHolder(site.id))
            for i, site in enumerate(target_sites)
        )
        + spectators,
        qubit_to_atom=binding,
    )
    return device, entry, target


@pytest.mark.parametrize(
    ("sources", "targets", "columns", "stationary", "batches"),
    [
        (((0, 0), (5, 5)), ((10, 20), (15, 25)), None, (), ((0, 1),)),
        (
            ((0, 0), (5, 0), (0, 5), (5, 5)),
            ((10, 20), (15, 20), (10, 25), (15, 25)),
            None,
            (),
            ((0, 1, 2, 3),),
        ),
        (((0, 0), (5, 0)), ((10, 20), (12, 20)), None, (), ((0,), (1,))),
        (
            ((0, 0), (5, 0), (10, 0)),
            ((10, 20), (15, 20), (20, 20)),
            2,
            (),
            ((0, 1), (2,)),
        ),
        (((0, 0), (5, 5)), ((10, 20), (15, 25)), None, ((0, 5),), ((0,), (1,))),
    ],
)
def test_strict_constructs_legal_rigid_batches_and_replays_to_requested_layout(
    sources: tuple[tuple[float, float], ...],
    targets: tuple[tuple[float, float], ...],
    columns: int | None,
    stationary: tuple[tuple[float, float], ...],
    batches: tuple[tuple[int, ...], ...],
) -> None:
    device, entry, target = _case(
        sources, targets, columns=columns, stationary=stationary
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert (
        tuple(
            tuple(atom for atom, _, _ in action.bindings)
            for step in fragment.steps
            for action in step.actions
            if isinstance(action, Load)
        )
        == batches
    )

    current = entry
    for step in fragment.steps:
        for action in step.actions:
            if isinstance(action, Load):
                checked = check_load(
                    device,
                    current,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
            elif isinstance(action, Move):
                checked = check_move(device, current, action.grid, dict(action.targets))
            else:
                assert isinstance(action, Store)
                checked = check_store(
                    device, current, action.grid, dict(action.bindings)
                )
            assert checked.ok, checked
        current = apply_step(current, step, device)
    assert current == target == fragment.exit_layout
    assert replay(AtomProgram(fragment.steps), entry, device) == target
