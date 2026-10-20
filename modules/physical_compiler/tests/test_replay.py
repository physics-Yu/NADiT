"""Atom IR replay applies planned transitions without repeating routing checks."""

import pytest

from nadit_physical_compiler import motion
from nadit_physical_compiler.atom_ir import (
    AtomProgram,
    AtomStep,
    CZPulse,
    Load,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
    Wait,
)
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
)
from nadit_physical_compiler.physical_ir import ResultRef
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.state import (
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)


@pytest.fixture
def device() -> Device:
    return Device(
        "replay",
        (),
        (
            SLMSite("s0", Position(0, 0)),
            SLMSite("s1", Position(5, 0)),
            SLMSite("g0", Position(0, 120)),
            SLMSite("g1", Position(2, 120)),
        ),
        (
            AOD("a", Region(-10, -10, 30, 250), 1),
            AOD("b", Region(-10, -10, 30, 250), 1),
        ),
        Timing(10, 12, 1),
    )


def _entry() -> AtomLayout:
    return AtomLayout(
        (
            AtomState(42, Position(0, 0), SLMHolder("s0")),
            AtomState(19, Position(5, 0), SLMHolder("s1")),
        ),
        qubit_to_atom=(19, 42),
    )


def _pair_grid(y: float, right: float) -> AODGrid:
    return AODGrid("a", (AODLine(8, y),), (AODLine(4, 0), AODLine(2, right)))


def test_transport_program_updates_holders_positions_and_active_lines(
    device: Device,
) -> None:
    entry = _entry()
    program = AtomProgram(
        (
            AtomStep(
                (Load(_pair_grid(0, 5), ((42, 8, 4), (19, 8, 2)), duration_us=10),)
            ),
            AtomStep(
                (
                    Move(
                        _pair_grid(120, 2),
                        ((42, Position(0, 120)), (19, Position(2, 120))),
                        duration_us=120,
                    ),
                )
            ),
            AtomStep(
                (
                    Store(
                        AODGrid("a"),
                        ((42, "g0"), (19, "g1")),
                        duration_us=12,
                    ),
                )
            ),
        )
    )
    loaded = apply_step(entry, program.steps[0], device)
    moved = apply_step(loaded, program.steps[1], device)
    final = replay(program, entry, device)

    assert loaded.atom(42).holder == AODHolder("a", 8, 4)
    assert loaded.atom(19).position == Position(5, 0)
    assert moved.atom(19) == AtomState(19, Position(2, 120), AODHolder("a", 8, 2))
    assert final == AtomLayout(
        (
            AtomState(42, Position(0, 120), SLMHolder("g0")),
            AtomState(19, Position(2, 120), SLMHolder("g1")),
        ),
        qubit_to_atom=(19, 42),
    )
    assert entry == _entry()
    for layout in (entry, loaded, moved, final):
        validate_layout(device, layout)


def test_parallel_actions_on_independent_aods_have_order_independent_effects(
    device: Device,
) -> None:
    grid_a = AODGrid("a", (AODLine(0, 0),), (AODLine(0, 0),))
    grid_b = AODGrid("b", (AODLine(0, 0),), (AODLine(0, 5),))
    actions = (
        Load(grid_b, ((19, 0, 0),), duration_us=10),
        Load(grid_a, ((42, 0, 0),), duration_us=10),
    )

    forward = apply_step(_entry(), AtomStep(actions), device)
    backward = apply_step(_entry(), AtomStep(tuple(reversed(actions))), device)

    assert forward == backward
    assert forward.atom(42).holder == AODHolder("a", 0, 0)
    assert forward.atom(19).holder == AODHolder("b", 0, 0)
    validate_layout(device, forward)


def test_partial_store_preserves_other_atoms_and_vacant_active_lines(
    device: Device,
) -> None:
    loaded = AtomLayout(
        (
            AtomState(42, Position(0, 120), AODHolder("a", 8, 4)),
            AtomState(19, Position(2, 120), AODHolder("a", 8, 2)),
        ),
        (_pair_grid(120, 2),),
        (19, 42),
    )
    remaining = AODGrid("a", (AODLine(8, 120),), (AODLine(2, 2), AODLine(9, 8)))
    step = AtomStep((Store(remaining, ((42, "g0"),), duration_us=12),))

    final = apply_step(loaded, step, device)

    assert final.atom(42).holder == SLMHolder("g0")
    assert final.atom(19) == loaded.atom(19)
    assert final.grid("a") == remaining
    validate_layout(device, final)


def test_grid_with_one_active_axis_is_not_discarded(device: Device) -> None:
    loaded = AtomLayout(
        (AtomState(42, Position(0, 0), AODHolder("a", 8, 4)),),
        (AODGrid("a", (AODLine(8, 0),), (AODLine(4, 0),)),),
        (42,),
    )
    rows_only = AODGrid("a", (AODLine(8, 0),))

    final = apply_step(
        loaded, AtomStep((Store(rows_only, ((42, "s0"),), duration_us=12),)), device
    )

    assert final.aod_grids == (rows_only,)
    validate_layout(device, final)


def test_pulses_waits_and_retained_measurements_preserve_layout(
    device: Device,
) -> None:
    entry = _entry()
    program = AtomProgram(
        (
            AtomStep((Rotation("raman", (42,), "X", 0.5, duration_us=1),)),
            AtomStep((CZPulse("rydberg", (42, 19), duration_us=0.5),)),
            AtomStep((Reset("raman", (19,), duration_us=2),)),
            AtomStep(
                (Measure("camera", ((42, ResultRef(100)),), True, duration_us=20),)
            ),
            AtomStep((Wait(duration_us=3),)),
        )
    )

    assert replay(program, entry, device) == entry


def test_destructive_measurement_requires_an_explicit_atom_lifecycle(
    device: Device,
) -> None:
    step = AtomStep(
        (Measure("camera", ((42, ResultRef(100)),), False, duration_us=20),)
    )

    with pytest.raises(NotImplementedError, match="retained"):
        apply_step(_entry(), step, device)


def test_replay_does_not_repeat_full_transport_checks(
    device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("Replay must apply constructed actions without full checks.")

    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, fail)
    step = AtomStep((Load(_pair_grid(0, 5), ((42, 8, 4), (19, 8, 2)), duration_us=10),))

    assert replay(AtomProgram((step,)), _entry(), device).grid("a") == _pair_grid(0, 5)


def test_empty_program_returns_entry_state(device: Device) -> None:
    entry = _entry()

    assert replay(AtomProgram(), entry, device) is entry
