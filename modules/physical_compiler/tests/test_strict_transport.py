"""Strict transport batching checked against the independent action validators."""

from dataclasses import replace
from math import hypot

import pytest

from nadit_physical_compiler import motion, state
from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.contracts import AtomFragment
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import (
    COORDINATE_TOLERANCE,
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.transport.strict import StrictTransport


def _case(
    sources: tuple[tuple[float, float], ...],
    targets: tuple[tuple[float, float], ...],
    *,
    stationary: tuple[tuple[float, float], ...] = (),
    rows: int | None = None,
    columns: int | None = None,
) -> tuple[Device, AtomLayout, AtomLayout]:
    source_sites = tuple(
        SLMSite(f"s{index}", Position(*point)) for index, point in enumerate(sources)
    )
    target_sites = tuple(
        SLMSite(f"t{index}", Position(*point)) for index, point in enumerate(targets)
    )
    static_sites = tuple(
        SLMSite(f"p{index}", Position(*point)) for index, point in enumerate(stationary)
    )
    device = Device(
        "strict",
        (),
        source_sites + target_sites + static_sites,
        (AOD("a", Region(-50, -50, 100, 250), 1, rows, columns),),
        Timing(3, 4, 2),
    )
    static_atoms = tuple(
        AtomState(100 + index, site.position, SLMHolder(site.id))
        for index, site in enumerate(static_sites)
    )
    ids = tuple(range(len(sources)))
    entry = AtomLayout(
        tuple(
            AtomState(index, site.position, SLMHolder(site.id))
            for index, site in enumerate(source_sites)
        )
        + static_atoms,
        qubit_to_atom=ids,
    )
    target = AtomLayout(
        tuple(
            AtomState(index, site.position, SLMHolder(site.id))
            for index, site in enumerate(target_sites)
        )
        + static_atoms,
        qubit_to_atom=ids,
    )
    return device, entry, target


def _loads(fragment: AtomFragment) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(atom for atom, _, _ in action.bindings)
        for step in fragment.steps
        for action in step.actions
        if isinstance(action, Load)
    )


def _verify(
    fragment: AtomFragment, entry: AtomLayout, target: AtomLayout, device: Device
) -> None:
    current = entry
    validate_layout(device, current)
    for step in fragment.steps:
        assert len(step.actions) == 1
        action = step.actions[0]
        if isinstance(action, Load):
            checked = check_load(
                device,
                current,
                action.grid,
                {atom: (row, column) for atom, row, column in action.bindings},
            )
        elif isinstance(action, Move):
            checked = check_move(device, current, action.grid, dict(action.targets))
            maximum_distance = max(
                hypot(
                    target_position.x - current.atom(atom).position.x,
                    target_position.y - current.atom(atom).position.y,
                )
                for atom, target_position in action.targets
            )
            assert action.duration_us == pytest.approx(
                device.timing.move_duration(maximum_distance)
            )
        else:
            assert isinstance(action, Store)
            checked = check_store(device, current, action.grid, dict(action.bindings))
        assert checked.ok, checked
        current = apply_step(current, step, device)
        validate_layout(device, current)
    assert current == target == fragment.exit_layout
    assert fragment.completed_operation_ids == ()


@pytest.mark.parametrize("full_grid", [False, True])
def test_sparse_and_full_two_dimensional_groups_share_one_load_move_store(
    full_grid: bool,
) -> None:
    sources = ((0, 0), (5, 5)) if not full_grid else ((0, 0), (5, 0), (0, 5), (5, 5))
    targets = tuple((2 * x + 10, 3 * y + 20) for x, y in sources)
    device, entry, target = _case(sources, targets)

    fragment = StrictTransport("a").construct(entry, target, device)

    assert _loads(fragment) == (tuple(range(len(sources))),)
    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Move, Store]
    _verify(fragment, entry, target, device)


def test_pending_source_ghost_joins_the_batch() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5), (0, 5)), ((10, 20), (15, 25), (10, 25))
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0, 1, 2),)
    _verify(fragment, entry, target, device)


@pytest.mark.parametrize("ghost_position", [(0, 5), (10, 25)])
def test_stationary_source_or_destination_ghost_forces_separate_batches(
    ghost_position: tuple[float, float],
) -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5)), ((10, 20), (15, 25)), stationary=(ghost_position,)
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0,), (1,))
    _verify(fragment, entry, target, device)


@pytest.mark.parametrize(
    ("last_source", "last_target"),
    [((0, 15), (10, 25)), ((20, 10), (30, 20))],
)
def test_ghost_closure_revisits_axes_after_adding_a_blocker(
    last_source: tuple[float, float], last_target: tuple[float, float]
) -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5), (10, 15), last_source),
        ((10, 10), (15, 15), (20, 25), last_target),
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0, 1, 2, 3),)
    _verify(fragment, entry, target, device)


def test_stationary_atom_found_late_in_closure_rejects_only_that_candidate() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5), (10, 15)),
        ((10, 10), (15, 15), (20, 25)),
        stationary=((0, 15),),
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0,), (1, 2))
    _verify(fragment, entry, target, device)


def test_completed_batch_positions_are_used_for_later_ghost_decisions() -> None:
    device, entry, target = _case(
        ((-5, -5), (0, 0), (5, 5)), ((10, 15), (10, 10), (15, 15))
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0,), (1,), (2,))
    _verify(fragment, entry, target, device)


def test_occupancy_queries_match_reference_coordinate_tolerance() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 5)),
        ((10, 20), (15, 25)),
        stationary=((10 + 0.5 * COORDINATE_TOLERANCE, 25),),
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0,), (1,))
    _verify(fragment, entry, target, device)


@pytest.mark.parametrize("axis", ["row", "column"])
def test_group_capacity_limits_three_pairwise_compatible_movements(axis: str) -> None:
    if axis == "row":
        sources, targets = ((0, 0), (0, 5), (0, 10)), ((10, 15), (10, 20), (10, 25))
        device, entry, target = _case(sources, targets, rows=2)
    else:
        sources, targets = ((0, 0), (5, 0), (10, 0)), ((15, 10), (20, 10), (25, 10))
        device, entry, target = _case(sources, targets, columns=2)
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0, 1), (2,))
    _verify(fragment, entry, target, device)


def test_joint_load_vacates_occupied_destinations() -> None:
    device, entry, target = _case(((0, 0), (5, 0), (10, 0)), ((5, 0), (10, 0), (15, 0)))
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((0, 1, 2),)
    _verify(fragment, entry, target, device)


def test_capacity_limited_chain_moves_from_vacant_end() -> None:
    device, entry, target = _case(
        ((0, 0), (5, 0), (10, 0)), ((5, 0), (10, 0), (15, 0)), columns=1
    )
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((2,), (1,), (0,))
    _verify(fragment, entry, target, device)


def test_incompatible_pending_blocker_moves_before_the_waiting_request() -> None:
    device, entry, target = _case(((0, 0), (5, 0)), ((5, 0), (5, 5)))
    fragment = StrictTransport("a").construct(entry, target, device)
    assert _loads(fragment) == ((1,), (0,))
    _verify(fragment, entry, target, device)


def test_closed_order_reversing_swap_requires_a_buffer() -> None:
    device, entry, target = _case(((0, 0), (5, 0)), ((5, 0), (0, 0)))
    with pytest.raises(ValueError, match="buffer"):
        StrictTransport("a").construct(entry, target, device)


def test_same_coordinates_with_new_slm_binding_omit_move() -> None:
    device, entry, target = _case(((0, 0), (5, 0)), ((0, 0), (5, 0)))
    fragment = StrictTransport("a").construct(entry, target, device)
    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Store]
    _verify(fragment, entry, target, device)


def test_unchanged_layout_emits_no_actions() -> None:
    device, entry, _ = _case(((0, 0), (5, 0)), ((10, 0), (15, 0)))
    fragment = StrictTransport("a").construct(entry, entry, device)
    assert fragment.steps == ()
    assert fragment.exit_layout == entry


def test_singleton_aod_readout_and_return_delegate_existing_transport() -> None:
    device, entry, _ = _case(((0, 0), (5, 0), (10, 0)), ((10, 10), (15, 10), (20, 10)))
    target = replace(
        entry,
        atoms=(
            entry.atoms[0],
            AtomState(1, Position(20, 200), AODHolder("a", 3, 8)),
            entry.atoms[2],
        ),
        aod_grids=(AODGrid("a", (AODLine(3, 200),), (AODLine(8, 20),)),),
    )
    transport = StrictTransport("a")
    arrival = transport.construct(entry, target, device)
    departure = transport.construct(arrival.exit_layout, entry, device)
    _verify(arrival, entry, target, device)
    _verify(departure, target, entry, device)


def test_constructed_actions_do_not_call_full_reference_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, entry, target = _case(((0, 0), (5, 5)), ((10, 20), (15, 25)))

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Strict construction must not call full validators.")

    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected)
    monkeypatch.setattr(state, "validate_layout", unexpected)
    monkeypatch.setattr(state, "validate_grid", unexpected)
    assert _loads(StrictTransport("a").construct(entry, target, device)) == ((0, 1),)
