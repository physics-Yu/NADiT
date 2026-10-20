"""Sparse gate-column batches are admitted constructively before generation."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.capabilities import CZCapability
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
from nadit_physical_compiler.transport import StagedStrictTransport, staged
from nadit_physical_compiler.transport.rules import MoveRequest
from nadit_physical_compiler.transport.staging import StageEvent, generate_stages
from nadit_physical_compiler.transport.staging_geometry import StagingGeometry


def test_sparse_gate_columns_never_send_the_unsafe_group_to_codegen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = Device(
        "sparse-gate-columns",
        (
            Zone("storage", Region(0, 0, 15, 15), ("load", "store")),
            Zone("gate", Region(0, 120, 10, 150), ("cz",)),
        ),
        slm_grid("source", Position(0, 0), 4, 4, Position(5, 5))
        + slm_grid("left", Position(0, 120), 4, 2, Position(8, 10))
        + slm_grid("right", Position(2, 120), 4, 2, Position(8, 10)),
        (AOD("a", Region(-20, -20, 40, 180), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )
    sources = (
        "source_r0_c0",
        "source_r1_c1",
        "source_r0_c2",
        "source_r1_c2",
        "source_r2_c3",
        "source_r3_c3",
    )
    targets = (
        "left_r0_c0",
        "right_r1_c0",
        "left_r0_c1",
        "left_r1_c1",
        "right_r2_c1",
        "right_r3_c1",
    )
    anchor = AtomState(
        99, device.site("right_r0_c1").position, SLMHolder("right_r0_c1")
    )
    entry = AtomLayout(
        tuple(
            AtomState(index, device.site(site).position, SLMHolder(site))
            for index, site in enumerate(sources)
        )
        + (anchor,),
        qubit_to_atom=tuple(range(6)),
    )
    target = replace(
        entry,
        atoms=tuple(
            AtomState(index, device.site(site).position, SLMHolder(site))
            for index, site in enumerate(targets)
        )
        + (anchor,),
    )
    generated_batches: list[tuple[MoveRequest, ...]] = []

    def record(
        moves: tuple[MoveRequest, ...],
        layout: AtomLayout,
        destinations: dict[int, AtomState],
        geometry: StagingGeometry,
    ) -> tuple[StageEvent, ...]:
        generated_batches.append(moves)
        source_rows = {move.source.y for move in moves}
        source_columns = {move.source.x for move in moves}
        target_columns: dict[float, set[float]] = {}
        for move in moves:
            target_columns.setdefault(move.target.x, set()).add(move.target.y)
        if len(source_columns) <= len(source_rows) and len(target_columns) > 2:
            masks = tuple(target_columns.values())
            assert all(mask == masks[0] for mask in masks)
        return generate_stages(moves, layout, destinations, geometry)

    monkeypatch.setattr(staged, "generate_stages", record)
    result = StagedStrictTransport("a").construct(entry, target, device)
    assert all(len(batch) < 6 for batch in generated_batches)
    current = entry
    for step in result.steps:
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
            assert checked.ok, checked.message
        current = apply_step(current, step, device)
        validate_layout(device, current)
        assert current.atom(99) == anchor
    assert current == target == result.exit_layout
