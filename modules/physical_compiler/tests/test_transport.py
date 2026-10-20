"""Legal construction of the two-atom transport profile."""

from dataclasses import replace
from math import hypot

import pytest

from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.contracts import AtomFragment, Transporter
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
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.transport import PairTransport


def _device(max_columns: int | None = None, min_spacing: float = 1) -> Device:
    return Device(
        "pair",
        (),
        (
            SLMSite("s0", Position(0, 0)),
            SLMSite("s1", Position(5, 0)),
            SLMSite("vertical", Position(5, 5)),
            SLMSite("g0", Position(10, 20)),
            SLMSite("g1", Position(12, 20)),
            SLMSite("spare", Position(30, 30)),
        ),
        (AOD("aod", Region(-10, -10, 50, 50), min_spacing, 1, max_columns),),
        Timing(3, 4, 2),
    )


def _stored(device: Device, first: str, second: str) -> AtomLayout:
    return AtomLayout(
        tuple(
            AtomState(atom_id, device.site(site_id).position, SLMHolder(site_id))
            for atom_id, site_id in ((2, first), (7, second))
        ),
        qubit_to_atom=(2, 7),
    )


def _readout(source: AtomLayout, position: Position) -> AtomLayout:
    grid = AODGrid("aod", (AODLine(4, position.y),), (AODLine(8, position.x),))
    return replace(
        source,
        atoms=(
            AtomState(2, position, AODHolder("aod", 4, 8)),
            source.atom(7),
        ),
        aod_grids=(grid,),
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
            result = check_load(
                device,
                current,
                action.grid,
                {atom: (row, column) for atom, row, column in action.bindings},
            )
        elif isinstance(action, Move):
            result = check_move(device, current, action.grid, dict(action.targets))
        else:
            assert isinstance(action, Store)
            result = check_store(device, current, action.grid, dict(action.bindings))
        assert result.ok, result
        current = apply_step(current, step, device)
        validate_layout(device, current)
    assert current == fragment.exit_layout == target
    assert fragment.completed_operation_ids == ()


def test_shared_row_pair_compresses_in_one_load_move_store() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    target = _stored(device, "g0", "g1")
    transporter: Transporter = PairTransport("aod")

    fragment = transporter.construct(entry, target, device)

    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Move, Store]
    load, move, store = (step.actions[0] for step in fragment.steps)
    assert isinstance(load, Load) and isinstance(move, Move)
    assert load.bindings == ((2, 0, 0), (7, 0, 1))
    assert move.duration_us == pytest.approx(2 * max(hypot(10, 20), hypot(7, 20)))
    assert load.duration_us == 3 and store.duration_us == 4
    _verify(fragment, entry, target, device)


@pytest.mark.parametrize(
    ("source_sites", "target_sites", "capacity", "min_spacing"),
    [
        (("s0", "vertical"), ("g0", "g1"), None, 1),
        (("s0", "s1"), ("g0", "g1"), 1, 1),
        (("s0", "s1"), ("g0", "g1"), None, 3),
        (("s0", "s1"), ("g1", "g0"), None, 1),
    ],
)
def test_incompatible_pair_uses_legal_singleton_transfers(
    source_sites: tuple[str, str],
    target_sites: tuple[str, str],
    capacity: int | None,
    min_spacing: float,
) -> None:
    device = _device(capacity, min_spacing)
    entry = _stored(device, *source_sites)
    target = _stored(device, *target_sites)

    fragment = PairTransport("aod").construct(entry, target, device)

    assert [type(step.actions[0]) for step in fragment.steps] == [Load, Move, Store] * 2
    _verify(fragment, entry, target, device)


def test_singleton_order_vacates_destination_before_filling_it() -> None:
    device = _device(max_columns=1)
    entry = _stored(device, "s0", "s1")
    target = _stored(device, "s1", "g0")

    fragment = PairTransport("aod").construct(entry, target, device)

    first = fragment.steps[0].actions[0]
    assert isinstance(first, Load)
    assert first.bindings == ((7, 0, 0),)
    _verify(fragment, entry, target, device)


def test_aod_readout_and_return_keep_explicit_line_identity() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    readout = _readout(entry, Position(20, 40))
    transporter = PairTransport("aod")

    outbound = transporter.construct(entry, readout, device)
    inbound = transporter.construct(outbound.exit_layout, entry, device)

    assert [type(step.actions[0]) for step in outbound.steps] == [Load, Move]
    assert [type(step.actions[0]) for step in inbound.steps] == [Move, Store]
    load = outbound.steps[0].actions[0]
    assert isinstance(load, Load)
    assert load.bindings == ((2, 4, 8),)
    _verify(outbound, entry, readout, device)
    _verify(inbound, readout, entry, device)


def test_transfer_at_current_position_does_not_emit_move() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    carried = _readout(entry, entry.atom(2).position)

    outbound = PairTransport("aod").construct(entry, carried, device)
    inbound = PairTransport("aod").construct(carried, entry, device)

    assert [type(step.actions[0]) for step in outbound.steps] == [Load]
    assert [type(step.actions[0]) for step in inbound.steps] == [Store]
    _verify(outbound, entry, carried, device)
    _verify(inbound, carried, entry, device)


def test_no_op_returns_no_steps_and_omits_empty_grids() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    with_empty_grid = replace(entry, aod_grids=(AODGrid("aod"),))

    fragment = PairTransport("aod").construct(with_empty_grid, entry, device)

    assert fragment.steps == ()
    assert fragment.exit_layout == entry


def test_closed_swap_requires_a_buffer_outside_this_profile() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    target = _stored(device, "s1", "s0")

    with pytest.raises(ValueError, match="swap|buffer"):
        PairTransport("aod").construct(entry, target, device)


def test_two_atom_profile_rejects_larger_layouts() -> None:
    device = _device()
    pair = _stored(device, "s0", "s1")
    third = AtomState(9, device.site("spare").position, SLMHolder("spare"))
    layout = replace(pair, atoms=(*pair.atoms, third))

    with pytest.raises(ValueError, match="two|2"):
        PairTransport("aod").construct(layout, layout, device)


def test_parked_aod_atom_is_not_implicitly_merged_with_new_transport() -> None:
    device = _device()
    entry = _readout(_stored(device, "s0", "s1"), Position(20, 40))
    target = replace(
        entry,
        atoms=(
            entry.atom(2),
            AtomState(7, device.site("g1").position, SLMHolder("g1")),
        ),
    )

    with pytest.raises(ValueError, match="AOD|singleton"):
        PairTransport("aod").construct(entry, target, device)


def test_requested_destination_must_be_inside_aod_workspace() -> None:
    device = _device()
    entry = _stored(device, "s0", "s1")
    target = _readout(entry, Position(80, 40))

    with pytest.raises(ValueError, match="workspace"):
        PairTransport("aod").construct(entry, target, device)
