"""Strict endpoint geometry shared by placement estimates and transport."""

from dataclasses import FrozenInstanceError, replace
from math import hypot

import pytest

from nadit_physical_compiler.device import AOD, Position, Region, Timing
from nadit_physical_compiler.state import COORDINATE_TOLERANCE, AODGrid, AODLine
from nadit_physical_compiler.transport.rules import (
    MoveRequest,
    StrictRules,
    build_conflict_map,
)


def _rules(
    *,
    spacing: float = 1,
    max_rows: int | None = None,
    max_columns: int | None = None,
) -> StrictRules:
    return StrictRules(
        AOD("transport", Region(0, 0, 100, 100), spacing, max_rows, max_columns),
        Timing(3, 4, 2),
    )


def _move(
    atom_id: int, source: tuple[float, float], target: tuple[float, float]
) -> MoveRequest:
    return MoveRequest(atom_id, Position(*source), Position(*target))


@pytest.mark.parametrize(
    ("first", "second", "compatible"),
    [
        (_move(0, (0, 0), (10, 20)), _move(1, (5, 0), (12, 20)), True),
        (_move(0, (0, 0), (10, 20)), _move(1, (0, 5), (10, 30)), True),
        (_move(0, (0, 0), (10, 20)), _move(1, (5, 5), (12, 30)), True),
        (_move(0, (0, 0), (10, 20)), _move(1, (5, 0), (12, 30)), False),
        (_move(0, (0, 0), (10, 20)), _move(1, (5, 5), (12, 20)), False),
        (_move(0, (0, 0), (10, 20)), _move(1, (0, 5), (12, 30)), False),
        (_move(0, (0, 0), (10, 20)), _move(1, (5, 5), (10, 30)), False),
        (_move(0, (0, 0), (12, 20)), _move(1, (5, 0), (10, 20)), False),
        (_move(0, (0, 0), (10, 30)), _move(1, (0, 5), (10, 20)), False),
    ],
)
def test_strict_compatibility_preserves_both_axis_relations(
    first: MoveRequest, second: MoveRequest, compatible: bool
) -> None:
    rules = _rules()

    assert rules.compatible(first, second) is compatible
    assert rules.compatible(second, first) is compatible


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ((0.5, 0), (12, 20)),
        ((5, 0), (10.5, 20)),
        ((0, 0.5), (10, 30)),
        ((0, 5), (10, 20.5)),
    ],
)
def test_distinct_rows_and_columns_respect_endpoint_spacing(
    source: tuple[float, float], target: tuple[float, float]
) -> None:
    first = _move(0, (0, 0), (10, 20))
    second = _move(1, source, target)

    assert not _rules().compatible(first, second)


@pytest.mark.parametrize("axis", ["row", "column"])
def test_pairwise_compatible_triple_can_exceed_batch_capacity(axis: str) -> None:
    moves = tuple(
        _move(
            index,
            (0, 5 * index) if axis == "row" else (5 * index, 0),
            (20, 20 + 2 * index) if axis == "row" else (20 + 2 * index, 20),
        )
        for index in range(3)
    )
    rules = _rules(
        max_rows=2 if axis == "row" else None,
        max_columns=2 if axis == "column" else None,
    )

    assert build_conflict_map(moves, rules) == {
        0: frozenset(),
        1: frozenset(),
        2: frozenset(),
    }
    assert rules.can_fit(moves[:2])
    assert not rules.can_fit(moves)


def test_frames_use_source_axis_order_and_preserve_input_binding_order() -> None:
    moves = (
        _move(7, (5, 10), (12, 30)),
        _move(2, (0, 0), (10, 20)),
        _move(9, (5, 0), (12, 20)),
    )
    rules = _rules()

    source, target, bindings = rules.frames(moves)

    assert rules.can_fit(moves)
    assert source == AODGrid(
        "transport", (AODLine(0, 0), AODLine(1, 10)), (AODLine(0, 0), AODLine(1, 5))
    )
    assert target == AODGrid(
        "transport",
        (AODLine(0, 20), AODLine(1, 30)),
        (AODLine(0, 10), AODLine(1, 12)),
    )
    assert bindings == ((7, 1, 1), (2, 0, 0), (9, 0, 1))
    for move, (_, row, column) in zip(moves, bindings, strict=True):
        assert source.position(row, column) == move.source
        assert target.position(row, column) == move.target


def test_batch_duration_uses_longest_distance_and_transfer_costs() -> None:
    moves = (_move(0, (0, 0), (3, 4)), _move(1, (10, 0), (16, 4)))
    rules = _rules()

    assert rules.duration_us(moves) == pytest.approx(3 + 4 + 2 * hypot(6, 4))
    assert rules.duration_us((_move(0, (0, 0), (0, 0)),)) == 7
    assert rules.duration_us(()) == 0


def test_empty_batch_has_empty_frames_and_fits() -> None:
    rules = _rules()

    assert rules.can_fit(())
    assert rules.frames(()) == (AODGrid("transport"), AODGrid("transport"), ())
    assert build_conflict_map((), rules) == {}


def test_conflict_map_has_symmetric_edges_and_isolated_nodes() -> None:
    moves = (
        _move(7, (0, 0), (12, 20)),
        _move(2, (5, 0), (10, 20)),
        _move(9, (10, 0), (20, 20)),
    )

    assert build_conflict_map(moves, _rules()) == {
        7: frozenset({2}),
        2: frozenset({7}),
        9: frozenset(),
    }


@pytest.mark.parametrize("at_source", [True, False])
def test_workspace_limits_apply_to_both_endpoints(at_source: bool) -> None:
    move = _move(0, (0, 0), (10, 20))
    outside = Position(101, 0)
    move = replace(move, source=outside) if at_source else replace(move, target=outside)

    assert not _rules().can_fit((move,))


def test_coordinate_tolerance_shares_one_line_at_both_endpoints() -> None:
    tolerance = COORDINATE_TOLERANCE
    moves = (
        _move(0, (0, 0), (10, 20)),
        _move(1, (0.8 * tolerance, 5), (10 + 0.6 * tolerance, 25)),
    )
    rules = _rules(max_columns=1)

    assert rules.compatible(*moves)
    assert rules.can_fit(moves)
    source, target, bindings = rules.frames(moves)
    assert len(source.columns) == len(target.columns) == 1
    assert bindings == ((0, 0, 0), (1, 1, 0))
    for move, (_, row, column) in zip(moves, bindings, strict=True):
        assert abs(source.position(row, column).x - move.source.x) <= tolerance
        assert abs(target.position(row, column).x - move.target.x) <= tolerance


def test_close_coordinate_chain_uses_fixed_representatives() -> None:
    tolerance = COORDINATE_TOLERANCE
    moves = tuple(
        _move(
            index, (factor * tolerance, 5 * index), (factor * tolerance, 20 + 5 * index)
        )
        for index, factor in enumerate((0, 0.75, 1.5))
    )
    rules = _rules(spacing=tolerance / 10, max_columns=1)

    assert all(not neighbors for neighbors in build_conflict_map(moves, rules).values())
    assert not rules.can_fit(moves)
    source, target, bindings = rules.frames(moves)
    assert len(source.columns) == len(target.columns) == 2
    assert [column for _, _, column in bindings] == [0, 0, 1]
    assert source.columns[1].coordinate == pytest.approx(1.5 * tolerance, abs=1e-20)


def test_minimum_spacing_and_workspace_use_the_coordinate_tolerance() -> None:
    tolerance = COORDINATE_TOLERANCE
    first = _move(0, (0, 0), (10, 20))
    close = _move(1, (1 - 0.5 * tolerance, 0), (11 - 0.5 * tolerance, 20))
    too_close = _move(1, (1 - 2 * tolerance, 0), (11 - 2 * tolerance, 20))
    rules = _rules()

    assert rules.compatible(first, close)
    assert rules.can_fit((first, close))
    assert not rules.compatible(first, too_close)
    assert not rules.can_fit((first, too_close))
    assert rules.can_fit(
        (_move(0, (-0.5 * tolerance, 0), (100 + 0.5 * tolerance, 100)),)
    )
    assert not rules.can_fit((_move(0, (-2 * tolerance, 0), (10, 20)),))


def test_requests_and_rules_are_immutable() -> None:
    request = _move(0, (0, 0), (10, 20))
    rules = _rules()

    with pytest.raises(FrozenInstanceError):
        request.atom_id = 4  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        rules.timing = Timing(1, 1, 1)  # type: ignore[misc]
