"""General singleton transport with occupied positions and static spectators."""

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
from nadit_physical_compiler.transport import SequentialTransport


def _device(count: int = 8) -> Device:
    return Device(
        "sequential",
        (),
        (
            *(
                SLMSite(f"s{index}", Position(5 * (index % 4), 5 * (index // 4)))
                for index in range(count)
            ),
            *(
                SLMSite(f"g{index}", Position(8 * (index // 2) + 2 * (index % 2), 120))
                for index in range(count)
            ),
            SLMSite("outside", Position(500, 500)),
        ),
        (AOD("aod", Region(-5, -5, 100, 250), 1, 1, 1),),
        Timing(3, 4, 2),
    )


def _stored(device: Device, sites: tuple[str, ...]) -> AtomLayout:
    atoms = tuple(
        AtomState(10 + index, device.site(site).position, SLMHolder(site))
        for index, site in enumerate(sites)
    )
    return AtomLayout(atoms, qubit_to_atom=tuple(atom.id for atom in atoms))


def _readout(layout: AtomLayout, atom_id: int, position: Position) -> AtomLayout:
    holder = AODHolder("aod", 3, 8)
    grid = AODGrid("aod", (AODLine(3, position.y),), (AODLine(8, position.x),))
    return replace(
        layout,
        atoms=tuple(
            AtomState(atom.id, position, holder) if atom.id == atom_id else atom
            for atom in layout.atoms
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
        assert isinstance(action, Load | Move | Store)
        assert len(action.grid.rows) <= 1 and len(action.grid.columns) <= 1
        if isinstance(action, Load):
            assert len(action.bindings) == 1
            result = check_load(
                device,
                current,
                action.grid,
                {atom: (row, column) for atom, row, column in action.bindings},
            )
        elif isinstance(action, Move):
            assert len(action.targets) == 1
            result = check_move(device, current, action.grid, dict(action.targets))
        else:
            assert len(action.bindings) == 1
            result = check_store(device, current, action.grid, dict(action.bindings))
        assert result.ok, result
        current = apply_step(current, step, device)
        validate_layout(device, current)
    assert current == fragment.exit_layout == target
    assert fragment.completed_operation_ids == ()


@pytest.mark.parametrize("count", [6, 8])
def test_many_atoms_use_singleton_transfers(count: int) -> None:
    device = _device(count)
    entry = _stored(device, tuple(f"s{index}" for index in range(count)))
    target = _stored(device, tuple(f"g{index}" for index in range(count)))
    transporter: Transporter = SequentialTransport("aod")

    fragment = transporter.construct(entry, target, device)

    assert [type(step.actions[0]) for step in fragment.steps] == [
        Load,
        Move,
        Store,
    ] * count
    for index in range(count):
        move = fragment.steps[3 * index + 1].actions[0]
        assert isinstance(move, Move)
        before, after = entry.atoms[index].position, target.atoms[index].position
        assert move.duration_us == pytest.approx(
            2 * hypot(after.x - before.x, after.y - before.y)
        )
    _verify(fragment, entry, target, device)


def test_singleton_grids_do_not_pick_up_cross_intersection_spectators() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _stored(device, ("g0", "s1", "s2", "s3", "s4", "g5", "s6", "s7"))

    fragment = SequentialTransport("aod").construct(entry, target, device)

    assert len(fragment.steps) == 6
    _verify(fragment, entry, target, device)


def test_vacancy_propagates_back_through_an_occupied_target_chain() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _stored(device, (*tuple(f"s{index}" for index in range(1, 8)), "g7"))

    fragment = SequentialTransport("aod").construct(entry, target, device)
    order = [
        action.bindings[0][0]
        for step in fragment.steps
        for action in step.actions
        if isinstance(action, Load)
    ]

    assert order == list(range(17, 9, -1))
    _verify(fragment, entry, target, device)


def test_singleton_readout_and_return_with_many_stationary_atoms() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _readout(entry, 14, Position(20, 220))
    transporter = SequentialTransport("aod")

    outbound = transporter.construct(entry, target, device)
    inbound = transporter.construct(outbound.exit_layout, entry, device)

    assert [type(step.actions[0]) for step in outbound.steps] == [Load, Move]
    assert [type(step.actions[0]) for step in inbound.steps] == [Move, Store]
    load = outbound.steps[0].actions[0]
    assert isinstance(load, Load) and load.bindings == ((14, 3, 8),)
    _verify(outbound, entry, target, device)
    _verify(inbound, target, entry, device)


def test_transfer_without_displacement_has_no_move() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _readout(entry, 14, entry.atom(14).position)

    outbound = SequentialTransport("aod").construct(entry, target, device)
    inbound = SequentialTransport("aod").construct(target, entry, device)

    assert [type(step.actions[0]) for step in outbound.steps] == [Load]
    assert [type(step.actions[0]) for step in inbound.steps] == [Store]
    _verify(outbound, entry, target, device)
    _verify(inbound, target, entry, device)


def test_no_op_omits_empty_grid_and_does_not_move_outside_spectators() -> None:
    device = _device()
    entry = _stored(device, ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "outside"))
    with_empty_grid = replace(entry, aod_grids=(AODGrid("aod"),))
    transporter = SequentialTransport("aod")

    assert transporter.construct(with_empty_grid, entry, device) == AtomFragment(
        (), entry
    )
    target = _stored(device, ("g0", "s1", "s2", "s3", "s4", "s5", "s6", "outside"))
    _verify(transporter.construct(entry, target, device), entry, target, device)


def test_closed_swap_reports_the_need_for_a_buffer() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _stored(device, ("s1", "s0", "s2", "s3", "s4", "s5", "s6", "s7"))

    with pytest.raises(ValueError, match="swap|buffer"):
        SequentialTransport("aod").construct(entry, target, device)


def test_target_slm_holder_must_match_its_requested_position() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = replace(
        entry,
        atoms=(replace(entry.atoms[0], holder=SLMHolder("g0")), *entry.atoms[1:]),
    )

    with pytest.raises(ValueError, match="SLM|holder"):
        SequentialTransport("aod").construct(entry, target, device)


def test_aod_readout_cannot_be_parked_during_another_atoms_transfer() -> None:
    device = _device()
    stored = _stored(device, tuple(f"s{index}" for index in range(8)))
    entry = _readout(stored, 14, Position(20, 220))
    target = replace(
        entry,
        atoms=(
            AtomState(10, device.site("g0").position, SLMHolder("g0")),
            *entry.atoms[1:],
        ),
    )

    with pytest.raises(ValueError, match="AOD|singleton"):
        SequentialTransport("aod").construct(entry, target, device)


def test_readout_grid_must_match_its_explicit_holder() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = replace(
        _readout(entry, 14, Position(20, 220)),
        aod_grids=(AODGrid("aod", (AODLine(0, 220),), (AODLine(0, 20),)),),
    )

    with pytest.raises(ValueError, match="AOD|bound"):
        SequentialTransport("aod").construct(entry, target, device)


def test_transport_requires_the_same_atoms_and_wire_mapping() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    different_atoms = replace(entry, atoms=entry.atoms[:-1], qubit_to_atom=())
    different_mapping = replace(
        entry, qubit_to_atom=tuple(reversed(entry.qubit_to_atom))
    )

    for target in (different_atoms, different_mapping):
        with pytest.raises(ValueError, match="identit|mapping"):
            SequentialTransport("aod").construct(entry, target, device)


def test_moving_atom_cannot_end_outside_the_aod_workspace() -> None:
    device = _device()
    entry = _stored(device, tuple(f"s{index}" for index in range(8)))
    target = _stored(device, ("outside", "s1", "s2", "s3", "s4", "s5", "s6", "s7"))

    with pytest.raises(ValueError, match="workspace"):
        SequentialTransport("aod").construct(entry, target, device)
