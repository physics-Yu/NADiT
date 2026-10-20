"""Relaxed endpoint relations and independently calculated offset costs."""

from dataclasses import FrozenInstanceError, replace

import pytest

from nadit_physical_compiler.device import AOD, Position, Region, Timing
from nadit_physical_compiler.state import COORDINATE_TOLERANCE
from nadit_physical_compiler.transport.relaxed_rules import (
    MovementCompatibility,
    RelaxedRules,
)
from nadit_physical_compiler.transport.rules import MoveRequest


def _rules(
    *, max_rows: int | None = None, max_columns: int | None = None
) -> RelaxedRules:
    return RelaxedRules(
        AOD("transport", Region(-100, -100, 200, 200), 1, max_rows, max_columns),
        Timing(3, 4, 2),
    )


def _move(
    atom_id: int, source: tuple[float, float], target: tuple[float, float]
) -> MoveRequest:
    return MoveRequest(atom_id, Position(*source), Position(*target))


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (
            _move(0, (0, 0), (2, 10)),
            _move(1, (5, 0), (8, 10)),
            MovementCompatibility("strict"),
        ),
        (
            _move(0, (0, 0), (8, 10)),
            _move(1, (5, 0), (2, 10)),
            MovementCompatibility("relaxed", 11),
        ),
        (
            _move(0, (0, 0), (10, 8)),
            _move(1, (0, 5), (10, 2)),
            MovementCompatibility("relaxed", 11),
        ),
        (
            _move(0, (0, 0), (8, 8)),
            _move(1, (5, 5), (2, 2)),
            MovementCompatibility("relaxed", 88),
        ),
        (
            _move(0, (0, 0), (0, 10)),
            _move(1, (0, 5), (5, 15)),
            MovementCompatibility("incompatible"),
        ),
        (
            _move(0, (0, 0), (0, 10)),
            _move(1, (5, 5), (0, 15)),
            MovementCompatibility("incompatible"),
        ),
        (
            _move(0, (0, 0), (10, 0)),
            _move(1, (5, 0), (15, 5)),
            MovementCompatibility("incompatible"),
        ),
        (
            _move(0, (0, 0), (10, 0)),
            _move(1, (5, 5), (15, 0)),
            MovementCompatibility("incompatible"),
        ),
    ],
)
def test_pair_classification_and_cost_are_symmetric(
    first: MoveRequest, second: MoveRequest, expected: MovementCompatibility
) -> None:
    rules = _rules()

    for left, right in ((first, second), (second, first)):
        actual = rules.compatibility(left, right)
        assert actual.status == expected.status
        if expected.merge_cost is None:
            assert actual.merge_cost is None
        else:
            assert actual.merge_cost == pytest.approx(expected.merge_cost)
        assert rules.compatible(left, right) is (expected.status != "incompatible")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ((0.5, 0), (2, 10)),
        ((5, 0), (7.5, 10)),
        ((0, 0.5), (10, 2)),
        ((0, 5), (10, 7.5)),
    ],
)
def test_order_reversal_still_requires_endpoint_spacing(
    source: tuple[float, float], target: tuple[float, float]
) -> None:
    first = _move(0, (0, 0), (8, 10)) if source[1] == 0 else _move(0, (0, 0), (10, 8))

    assert _rules().compatibility(first, _move(1, source, target)).status == (
        "incompatible"
    )


def test_coordinate_tolerance_preserves_shared_lines_during_reversal() -> None:
    tolerance = COORDINATE_TOLERANCE
    moves = (
        _move(0, (0, 0), (10, 8)),
        _move(1, (0.8 * tolerance, 5), (10 + 0.6 * tolerance, 2)),
    )
    rules = _rules(max_columns=1)

    assert rules.compatibility(*moves) == MovementCompatibility("relaxed", 11)
    assert rules.can_fit(moves)


def test_endpoint_capacity_allows_reversal_but_limits_distinct_lines() -> None:
    moves = tuple(
        _move(index, (5 * index, 0), (10 - 5 * index, 100)) for index in range(3)
    )
    rules = _rules(max_columns=2)

    assert all(
        rules.compatible(first, second)
        for index, first in enumerate(moves)
        for second in moves[index + 1 :]
    )
    assert rules.can_fit(moves[:2])
    assert not rules.can_fit(moves)


@pytest.mark.parametrize("at_source", [True, False])
def test_endpoint_workspace_is_checked_for_relaxed_requests(at_source: bool) -> None:
    move = _move(0, (0, 0), (10, 20))
    outside = Position(201, 0)
    move = replace(move, source=outside) if at_source else replace(move, target=outside)

    assert not _rules().can_fit((move,))


def test_offset_cost_is_independent_of_device_timing() -> None:
    moves = (_move(0, (0, 0), (8, 8)), _move(1, (5, 5), (2, 2)))
    rules = _rules()

    assert rules.compatibility(*moves) == replace(
        rules, timing=Timing(100, 200, 300)
    ).compatibility(*moves)
    assert rules.can_fit(())


def test_compatibility_result_and_rules_are_immutable() -> None:
    result = MovementCompatibility("relaxed", 11)
    rules = _rules()

    with pytest.raises(FrozenInstanceError):
        result.merge_cost = 20  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        rules.timing = Timing(1, 1, 1)  # type: ignore[misc]
