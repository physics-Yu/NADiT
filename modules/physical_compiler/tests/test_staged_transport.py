"""Staged transfers avoid occupied cross-intersections of regular grids."""

from dataclasses import replace
from math import hypot

import pytest

from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.contracts import AtomFragment
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    Timing,
    Zone,
    slm_grid,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import (
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.transport import (
    StagedStrictTransport,
    StrictTransport,
)


def _device() -> Device:
    return Device(
        "occupied-cross-grid",
        (
            Zone("storage", Region(0, 0, 5, 5), ("load", "store")),
            Zone("target", Region(20, 120, 22, 122), ("load", "store")),
        ),
        (
            *slm_grid("source", Position(0, 0), 2, 2, Position(5, 5)),
            *slm_grid("target", Position(20, 120), 2, 2, Position(2, 2)),
        ),
        (AOD("transport", Region(-10, -10, 50, 150), 1, 2, 2),),
        Timing(10, 12, 1),
    )


def _layouts(device: Device) -> tuple[AtomLayout, AtomLayout]:
    entry = AtomLayout(
        tuple(
            AtomState(atom_id, device.site(site).position, SLMHolder(site))
            for atom_id, site in (
                (37, "source_r0_c0"),
                (50, "source_r1_c1"),
                (63, "source_r0_c1"),
                (76, "source_r1_c0"),
            )
        ),
        qubit_to_atom=(37, 50),
    )
    target = replace(
        entry,
        atoms=(
            AtomState(
                37, device.site("target_r0_c0").position, SLMHolder("target_r0_c0")
            ),
            AtomState(
                50, device.site("target_r1_c1").position, SLMHolder("target_r1_c1")
            ),
            *entry.atoms[2:],
        ),
    )
    return entry, target


def _audit_fragment(
    fragment: AtomFragment,
    entry: AtomLayout,
    target: AtomLayout,
    device: Device,
) -> None:
    layout = entry
    validate_layout(device, entry)
    for step in fragment.steps:
        for action in step.actions:
            assert isinstance(action, Load | Move | Store)
            if isinstance(action, Load):
                checked = check_load(
                    device,
                    layout,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
                assert action.duration_us == device.timing.load_us
            elif isinstance(action, Move):
                checked = check_move(device, layout, action.grid, dict(action.targets))
                distances = (
                    hypot(
                        position.x - layout.atom(atom).position.x,
                        position.y - layout.atom(atom).position.y,
                    )
                    for atom, position in action.targets
                )
                assert action.duration_us == pytest.approx(
                    device.timing.move_duration(max(distances, default=0))
                )
            else:
                checked = check_store(
                    device, layout, action.grid, dict(action.bindings)
                )
                assert action.duration_us == device.timing.store_us
            assert checked.ok, checked.message
        layout = apply_step(layout, step, device)
        validate_layout(device, layout)
        for atom_id in (63, 76):
            assert layout.atom(atom_id) == entry.atom(atom_id)
    assert layout == fragment.exit_layout == target
    assert fragment.completed_operation_ids == ()


def test_staging_carries_diagonal_atoms_together_without_loading_cross_spectators() -> (
    None
):
    device = _device()
    entry, target = _layouts(device)
    staged = StagedStrictTransport("transport")
    direct = StrictTransport("transport").construct(entry, target, device)
    outbound = staged.construct(entry, target, device)
    inbound = staged.construct(outbound.exit_layout, entry, device)

    _audit_fragment(direct, entry, target, device)
    _audit_fragment(outbound, entry, target, device)
    _audit_fragment(inbound, target, entry, device)
    direct_moves = [
        action
        for step in direct.steps
        for action in step.actions
        if isinstance(action, Move)
    ]
    assert all(len(action.targets) == 1 for action in direct_moves)
    for fragment in (outbound, inbound):
        actions = [action for step in fragment.steps for action in step.actions]
        assert any(
            isinstance(action, Move) and len(action.targets) == 2 for action in actions
        )
        assert {
            atom
            for action in actions
            if isinstance(action, Load)
            for atom, _, _ in action.bindings
        } == {37, 50}
        assert {
            atom
            for action in actions
            if isinstance(action, Store)
            for atom, _ in action.bindings
        } == {37, 50}
