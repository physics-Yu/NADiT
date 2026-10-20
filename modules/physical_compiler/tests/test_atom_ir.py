"""Tests for scheduled atom actions and deterministic text output."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from nadit_physical_compiler import motion, state
from nadit_physical_compiler.atom_ir import (
    AtomAction,
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
from nadit_physical_compiler.device import Position, Region
from nadit_physical_compiler.physical_ir import ResultRef
from nadit_physical_compiler.state import AODGrid, AODLine


def _grid() -> AODGrid:
    return AODGrid(
        "a",
        rows=(AODLine(0, 0),),
        columns=(AODLine(0, 0), AODLine(1, 5)),
    )


def test_empty_program() -> None:
    program = AtomProgram()

    assert program.steps == ()
    assert program.duration_us == 0
    assert program.timeline() == ()
    assert program.to_text() == ""


def test_parallel_steps_use_maximum_action_duration() -> None:
    first = AtomStep((Wait(duration_us=3), Wait(duration_us=7)))
    second = AtomStep((Wait(duration_us=2),))
    program = AtomProgram((first, second))

    assert first.duration_us == 7
    assert program.duration_us == 9
    assert program.timeline() == ((0, first), (7, second))
    assert program.to_text() == (
        "WAIT(duration_us=3)\n"
        "WAIT(duration_us=7)\n"
        "TICK(duration_us=7)\n"
        "WAIT(duration_us=2)\n"
        "TICK(duration_us=2)\n"
    )


@pytest.mark.parametrize(
    ("action", "line", "duration"),
    (
        (
            Load(_grid(), ((0, 0, 0), (1, 0, 1)), duration_us=10),
            'LOAD(duration_us=10,aod="a",rows=[[0,0]],columns=[[0,0],[1,5]]) '
            "0->(0,0) 1->(0,1)",
            10,
        ),
        (
            Move(_grid(), ((0, Position(0, 120)),), duration_us=12),
            'MOVE(duration_us=12,aod="a",rows=[[0,0]],columns=[[0,0],[1,5]]) '
            "0->(0,120)",
            12,
        ),
        (
            Store(_grid(), ((0, "site"),), duration_us=10),
            'STORE(duration_us=10,aod="a",rows=[[0,0]],columns=[[0,0],[1,5]]) '
            '0->"site"',
            10,
        ),
        (
            CZPulse("rydberg", Region(0, 119, 10, 121), duration_us=0.5),
            'CZ_PULSE(duration_us=0.5,channel="rydberg",region=[0,119,10,121])',
            0.5,
        ),
        (
            CZPulse("local", (0, 1), duration_us=0.5),
            'CZ_PULSE(duration_us=0.5,channel="local") 0 1',
            0.5,
        ),
        (
            Rotation("raman", (0,), "X", 0.5, duration_us=1),
            'ROT(duration_us=1,channel="raman",axis="X",angle=0.5) 0',
            1,
        ),
        (
            Reset("reset", (0,), duration_us=1),
            'R(duration_us=1,channel="reset") 0',
            1,
        ),
        (
            Measure("readout", ((0, ResultRef(42)),), True, duration_us=10),
            'M(duration_us=10,channel="readout",retain_atoms=true) 0->rec[42]',
            10,
        ),
        (
            Wait(duration_us=3),
            "WAIT(duration_us=3)",
            3,
        ),
    ),
)
def test_action_text(action: AtomAction, line: str, duration: float) -> None:
    program = AtomProgram((AtomStep((action,)),))

    assert program.to_text() == f"{line}\nTICK(duration_us={duration})\n"


def test_empty_step_remains_an_explicit_tick() -> None:
    empty = AtomStep()
    program = AtomProgram((empty, AtomStep((Wait(duration_us=4),)), empty))

    assert program.timeline() == (
        (0, empty),
        (0, program.steps[1]),
        (4, empty),
    )
    assert program.duration_us == 4
    assert program.to_text() == (
        "TICK(duration_us=0)\n"
        "WAIT(duration_us=4)\n"
        "TICK(duration_us=4)\n"
        "TICK(duration_us=0)\n"
    )


def test_wait_places_idle_time_between_steps() -> None:
    rotation = AtomStep((Rotation("r", (5,), "Y", 0.5, duration_us=2),))
    idle = AtomStep((Wait(duration_us=8),))
    measurement = AtomStep((Measure("m", ((5, ResultRef(12)),), True, duration_us=10),))
    program = AtomProgram((rotation, idle, measurement))

    assert program.timeline() == ((0, rotation), (2, idle), (10, measurement))
    assert program.duration_us == 20


def test_measurement_keeps_source_identity_and_retention_choice() -> None:
    action = Measure(
        "readout",
        ((7, ResultRef(80)), (2, ResultRef(19))),
        False,
        duration_us=6,
        source_ids=(80, 19),
    )
    program = AtomProgram((AtomStep((action,)),))

    assert action.results == ((7, ResultRef(80)), (2, ResultRef(19)))
    assert action.source_ids == (80, 19)
    assert program.to_text() == (
        'M(duration_us=6,channel="readout",retain_atoms=false) '
        "7->rec[80] 2->rec[19] # source_ids=80,19\n"
        "TICK(duration_us=6)\n"
    )


def test_source_provenance_is_shared_by_transport_actions() -> None:
    action = Move(
        _grid(),
        ((1, Position(5, 120)), (0, Position(0, 120))),
        duration_us=12,
        source_ids=(3, 4),
    )

    assert AtomProgram((AtomStep((action,)),)).to_text() == (
        'MOVE(duration_us=12,aod="a",rows=[[0,0]],columns=[[0,0],[1,5]]) '
        "1->(5,120) 0->(0,120) # source_ids=3,4\n"
        "TICK(duration_us=12)\n"
    )


def test_endpoint_grid_preserves_empty_lines_and_input_order() -> None:
    grid = AODGrid(
        "a",
        rows=(AODLine(8, 15), AODLine(2, 0)),
        columns=(AODLine(7, 5), AODLine(0, 0)),
    )
    action = Load(grid, ((4, 2, 0),), duration_us=1)

    assert action.grid is grid
    assert AtomProgram((AtomStep((action,)),)).to_text() == (
        'LOAD(duration_us=1,aod="a",rows=[[8,15],[2,0]],columns=[[7,5],[0,0]]) '
        "4->(2,0)\n"
        "TICK(duration_us=1)\n"
    )


def test_store_can_leave_no_active_lines() -> None:
    action = Store(AODGrid("a"), ((4, "storage:2"),), duration_us=1)

    assert AtomProgram((AtomStep((action,)),)).to_text() == (
        'STORE(duration_us=1,aod="a",rows=[],columns=[]) 4->"storage:2"\n'
        "TICK(duration_us=1)\n"
    )


def test_text_escapes_strings_without_changing_identifiers() -> None:
    grid = AODGrid('a"\\\n中')
    action = Store(grid, ((0, 's"\\\n文'),), duration_us=1)
    pulse = CZPulse('c"\\\n光', (0,), duration_us=1)
    program = AtomProgram((AtomStep((action, pulse)),))

    assert program.to_text() == (
        'STORE(duration_us=1,aod="a\\"\\\\\\n\\u4e2d",rows=[],columns=[]) '
        '0->"s\\"\\\\\\n\\u6587"\n'
        'CZ_PULSE(duration_us=1,channel="c\\"\\\\\\n\\u5149") 0\n'
        "TICK(duration_us=1)\n"
    )
    assert action.grid.aod_id == 'a"\\\n中'


def test_text_preserves_small_angles_and_global_phase() -> None:
    action = Rotation("r", (0,), "Z", 1e-25, duration_us=0)
    program = AtomProgram((AtomStep((action,)),), global_phase=1e-30)

    assert program.to_text() == (
        "# global_phase=1e-30\n"
        'ROT(duration_us=0,channel="r",axis="Z",angle=1e-25) 0\n'
        "TICK(duration_us=0)\n"
    )
    assert AtomProgram(global_phase=0.25).to_text() == "# global_phase=0.25\n"


def test_construction_and_text_do_not_call_device_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_check(*args: object, **kwargs: object) -> None:
        raise AssertionError("Atom IR must not revalidate planned device actions.")

    for name in ("check_load", "check_move", "check_store"):
        monkeypatch.setattr(motion, name, unexpected_check)
    monkeypatch.setattr(state, "validate_layout", unexpected_check)
    move = Move(_grid(), ((0, Position(0, 120)),), duration_us=2)
    actions: tuple[AtomAction, ...] = (
        Load(_grid(), ((0, 0, 0),), duration_us=1),
        move,
        Store(AODGrid("a"), ((0, "s"),), duration_us=1),
    )
    program = AtomProgram(tuple(AtomStep((action,)) for action in actions))

    text = program.to_text()
    assert text == program.to_text()
    assert program.duration_us == 4
    assert program.timeline()[-1][0] == 3
    assert move.targets == ((0, Position(0, 120)),)


def test_scheduled_actions_trust_producer_parallelism() -> None:
    step = AtomStep(
        (
            Rotation("shared", (0,), "X", 0.5, duration_us=1),
            Rotation("shared", (0,), "Z", 0.5, duration_us=1),
        )
    )

    assert step.duration_us == 1
    assert AtomProgram((step,)).to_text().count("ROT(") == 2


def test_records_are_immutable_and_slotted() -> None:
    action = Wait(duration_us=1)
    step = AtomStep((action,))
    program = AtomProgram((step,))

    for record, field in (
        (action, "duration_us"),
        (step, "actions"),
        (program, "steps"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, None)
        assert not hasattr(record, "__dict__")


@pytest.mark.parametrize(
    "factory",
    (
        lambda duration: Load(_grid(), (), duration_us=duration),
        lambda duration: Move(_grid(), (), duration_us=duration),
        lambda duration: Store(_grid(), (), duration_us=duration),
        lambda duration: CZPulse("c", (), duration_us=duration),
        lambda duration: Rotation("r", (), "X", 0, duration_us=duration),
        lambda duration: Reset("r", (), duration_us=duration),
        lambda duration: Measure("m", (), True, duration_us=duration),
        lambda duration: Wait(duration_us=duration),
    ),
)
def test_actions_reject_negative_duration(
    factory: Callable[[float], AtomAction],
) -> None:
    with pytest.raises(ValueError, match="duration"):
        factory(-1)


@pytest.mark.parametrize("duration", (nan, inf, -inf))
def test_nonfinite_duration_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        Wait(duration_us=duration)


@pytest.mark.parametrize("angle", (nan, inf, -inf))
def test_nonfinite_angle_is_rejected(angle: float) -> None:
    with pytest.raises(ValueError, match="angle"):
        Rotation("r", (0,), "X", angle, duration_us=1)


@pytest.mark.parametrize("phase", (nan, inf, -inf))
def test_nonfinite_global_phase_is_rejected(phase: float) -> None:
    with pytest.raises(ValueError, match="phase"):
        AtomProgram(global_phase=phase)
