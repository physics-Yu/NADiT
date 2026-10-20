"""Reference donor costs and all-or-nothing device admission."""

import pytest

from nadit_physical_compiler.device import AOD, Position, Region, Timing
from nadit_physical_compiler.transport.relaxed_groups import (
    MovementGroups,
    route_relaxed,
)
from nadit_physical_compiler.transport.relaxed_rules import RelaxedRules
from nadit_physical_compiler.transport.rules import MoveRequest


def _rules(*, max_columns: int | None = None) -> RelaxedRules:
    return RelaxedRules(
        AOD("transport", Region(-100, -100, 200, 200), 1, None, max_columns),
        Timing(3, 4, 2),
    )


def _move(
    atom_id: int, source: tuple[float, float], target: tuple[float, float]
) -> MoveRequest:
    return MoveRequest(atom_id, Position(*source), Position(*target))


def _ids(groups: MovementGroups) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(move.atom_id for move in group) for group in groups)


def test_strict_initial_order_uses_descending_distance_then_atom_id() -> None:
    moves = (
        _move(2, (10, 0), (10, 100)),
        _move(0, (0, 0), (0, 100)),
        _move(1, (5, 0), (6, 100)),
    )

    assert _ids(route_relaxed(moves, _rules())) == ((1, 2, 0),)


@pytest.mark.parametrize(
    ("vertical_distance", "prefer_split", "expected"),
    [
        (100, 1.0, ((1, 0),)),
        (100, 10.0, ((1,), (0,))),
        (0, 1.0, ((1,), (0,))),
        (0, 0.0, ((1, 0),)),
        (0, 0.5, ((1,), (0,))),
    ],
)
def test_reversal_merge_uses_strict_cubed_offset_threshold(
    vertical_distance: float,
    prefer_split: float,
    expected: tuple[tuple[int, ...], ...],
) -> None:
    moves = (
        _move(0, (0, 0), (10, vertical_distance)),
        _move(1, (10, 0), (0, vertical_distance)),
    )

    assert _ids(route_relaxed(moves, _rules(), prefer_split=prefer_split)) == expected


def test_equality_conflict_cannot_merge_even_when_split_penalty_is_zero() -> None:
    moves = (
        _move(0, (0, 0), (0, 100)),
        _move(1, (0, 5), (10, 105)),
    )

    assert _ids(route_relaxed(moves, _rules(), prefer_split=0)) == ((1,), (0,))


def test_finite_capacity_prevents_otherwise_profitable_merge() -> None:
    moves = (_move(0, (0, 0), (10, 100)), _move(1, (10, 0), (0, 100)))

    assert _ids(route_relaxed(moves, _rules(max_columns=1))) == ((1,), (0,))


def test_initial_groups_keep_the_supplied_recipient_order() -> None:
    moves = (_move(0, (0, 0), (0, 100)), _move(1, (5, 0), (5, 100)))
    initial = ((moves[0],), (moves[1],))

    result = route_relaxed(moves, _rules(), initial_groups=initial)

    assert _ids(result) == ((0, 1),)
    assert result[0][0] is moves[0]
    assert result[0][1] is moves[1]


def test_rejected_proposals_leave_donor_and_receivers_unchanged() -> None:
    moves = (_move(0, (0, 0), (10, 100)), _move(1, (10, 0), (0, 100)))
    initial = ((moves[1],), (moves[0],))
    proposals: list[MovementGroups] = []

    def reject(groups: MovementGroups) -> bool:
        proposals.append(groups)
        return False

    result = route_relaxed(moves, _rules(), initial_groups=initial, admit=reject)

    assert result == initial
    assert len(proposals) == 2
    assert all(len(proposal) == 1 for proposal in proposals)
    assert all(
        sorted(move.atom_id for move in proposal[0]) == [0, 1] for proposal in proposals
    )


def test_later_rejection_preserves_an_earlier_accepted_donor_merge() -> None:
    moves = (
        _move(0, (0, 0), (10, 100)),
        _move(1, (5, 0), (5, 100)),
        _move(2, (10, 0), (0, 100)),
    )
    initial = ((moves[2],), (moves[1],), (moves[0],))
    proposals: list[MovementGroups] = []

    def accept_first(groups: MovementGroups) -> bool:
        proposals.append(groups)
        return len(proposals) == 1

    result = route_relaxed(
        moves, _rules(), prefer_split=0, initial_groups=initial, admit=accept_first
    )

    assert _ids(result) == ((2,), (1, 0))
    assert len(proposals) == 3
    assert all(
        sorted(move.atom_id for group in proposal for move in group) == [0, 1, 2]
        for proposal in proposals
    )
    assert _ids(proposals[0]) == ((2,), (1, 0))


def test_one_donor_can_split_between_two_compatible_recipient_groups() -> None:
    moves = (
        _move(0, (0, 0), (0, 100)),
        _move(1, (10, 0), (10, 100)),
        _move(2, (0, 10), (5, 110)),
        _move(3, (10, 10), (15, 110)),
    )
    initial = ((moves[0],), (moves[1],), (moves[2], moves[3]))

    result = route_relaxed(moves, _rules(), initial_groups=initial)

    assert _ids(result) == ((0, 3), (1, 2))


def test_combined_donor_capacity_is_checked_before_admission() -> None:
    moves = tuple(_move(index, (5 * index, 0), (5 * index, 100)) for index in range(4))
    initial = (moves[:2], moves[2:])
    proposals: list[MovementGroups] = []

    def record(groups: MovementGroups) -> bool:
        proposals.append(groups)
        return True

    result = route_relaxed(
        moves, _rules(max_columns=3), initial_groups=initial, admit=record
    )

    assert result == initial
    assert not proposals


def test_multi_atom_donor_cost_accumulates_before_comparing_threshold() -> None:
    moves = (
        _move(0, (0, 0), (2, 120)),
        _move(1, (5, 0), (0, 120)),
        _move(2, (0, 5), (2, 130)),
        _move(3, (5, 5), (0, 130)),
    )

    merged = route_relaxed(moves, _rules())
    separate = route_relaxed(moves, _rules(), prefer_split=3)

    assert len(merged) == 1
    assert {move.atom_id for move in merged[0]} == {0, 1, 2, 3}
    assert _ids(separate) == ((3, 1), (2, 0))


def test_empty_routing_has_no_admission_proposals() -> None:
    def unexpected_admission(groups: MovementGroups) -> bool:
        raise AssertionError(f"No donor exists: {groups}")

    assert route_relaxed((), _rules(), admit=unexpected_admission) == ()


def test_unreachable_single_request_reports_failure_without_retrying() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        route_relaxed((_move(0, (0, 0), (201, 0)),), _rules())
