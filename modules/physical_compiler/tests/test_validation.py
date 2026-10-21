"""Reference result validation reports the first concrete action failure."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from examples.viewer_cases import compile_feedback
from nadit_physical_compiler import (
    AOD,
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomProgram,
    AtomState,
    AtomStep,
    CompilationResult,
    CompiledOperation,
    Device,
    FakeBackend,
    Load,
    Measure,
    Move,
    Position,
    Region,
    ResultRef,
    Rotation,
    SLMHolder,
    SLMSite,
    Store,
    Timing,
    Wait,
    compile_physical,
    load_device,
    load_qasm,
)
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import PairTransport
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]


def _device(*sites: SLMSite) -> Device:
    return Device(
        "validation",
        zones=(),
        slm_sites=sites,
        aods=(AOD("transport", Region(0, 0, 100, 100), min_spacing=1),),
        timing=Timing(load_us=1, store_us=1, move_us_per_um=1),
    )


def _grid(
    rows: tuple[tuple[int, float], ...],
    columns: tuple[tuple[int, float], ...],
) -> AODGrid:
    return AODGrid(
        "transport",
        tuple(AODLine(key, coordinate) for key, coordinate in rows),
        tuple(AODLine(key, coordinate) for key, coordinate in columns),
    )


def _carried(atom: int, grid: AODGrid, row: int, column: int) -> AtomState:
    return AtomState(
        atom, grid.position(row, column), AODHolder("transport", row, column)
    )


def _stored(atom: int, site: SLMSite) -> AtomState:
    return AtomState(atom, site.position, SLMHolder(site.id))


def _result(
    program: AtomProgram,
    exit_state: AtomLayout,
    operations: tuple[CompiledOperation, ...] = (),
) -> CompilationResult:
    return CompilationResult(program, exit_state, operations, (), ())


@pytest.mark.parametrize("bit", [0, 1])
def test_compiled_feedback_examples_pass_without_mutation(bit: int) -> None:
    example = compile_feedback(bit)
    original = deepcopy((example.result, example.entry))

    report = validate_result(
        example.result, entry_state=example.entry, device=example.device
    )

    assert report == {"ok": True, "failure": None}
    assert json.loads(json.dumps(report)) == report
    assert (example.result, example.entry) == original


@pytest.mark.parametrize("bit", [0, 1])
def test_single_pair_feedback_composition_passes(bit: int) -> None:
    device = load_device(MODULE / "configs/one_pair_flexible.json")
    dag = load_qasm(MODULE / "examples/pair_feedback.qasm3")
    entry = AtomLayout(
        tuple(
            _stored(atom, device.site(site))
            for atom, site in ((7, "storage_r0_c0"), (11, "storage_r0_c1"))
        ),
        qubit_to_atom=(7, 11),
    )
    overrides = {node.result: bit for node in dag.nodes if node.result is not None}
    result = compile_physical(
        dag,
        device,
        entry_state=entry,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
        ),
        measurement_backend=FakeBackend(17, overrides),
    )

    assert validate_result(result, entry_state=entry, device=device)["ok"]


def test_missing_carried_atom_reports_motion_rule_and_action_location() -> None:
    source = _grid(((4, 10),), ((3, 10), (8, 20)))
    target = _grid(((4, 30),), ((3, 10), (8, 20)))
    entry = AtomLayout(
        (_carried(0, source, 4, 3), _carried(1, source, 4, 8)), (source,)
    )
    program = AtomProgram(
        (
            AtomStep((Wait(duration_us=2),)),
            AtomStep(
                (
                    Wait(duration_us=1),
                    Move(target, ((0, Position(10, 30)),), duration_us=20),
                )
            ),
        )
    )

    report = validate_result(
        _result(program, entry), entry_state=entry, device=_device()
    )

    assert report == {
        "ok": False,
        "failure": {
            "step": 1,
            "action": 1,
            "kind": "MOVE",
            "rule": "omitted_atom",
            "message": "Active lines also move atom 1.",
        },
    }


def test_diagonal_loading_reports_ghost_intersection() -> None:
    sites = (
        SLMSite("first", Position(10, 10)),
        SLMSite("second", Position(20, 20)),
        SLMSite("spectator", Position(20, 10)),
    )
    entry = AtomLayout(tuple(_stored(atom, site) for atom, site in enumerate(sites)))
    grid = _grid(((4, 10), (9, 20)), ((3, 10), (8, 20)))
    program = AtomProgram(
        (AtomStep((Load(grid, ((0, 4, 3), (1, 9, 8)), duration_us=1),)),)
    )

    report = validate_result(
        _result(program, entry), entry_state=entry, device=_device(*sites)
    )

    assert report["failure"] == {
        "step": 0,
        "action": 0,
        "kind": "LOAD",
        "rule": "ghost_pickup",
        "message": "An active intersection also traps SLM atom 2.",
    }


def test_store_reports_a_trap_that_remains_active() -> None:
    grid = _grid(((4, 10),), ((3, 10),))
    site = SLMSite("destination", Position(10, 10))
    entry = AtomLayout((_carried(0, grid, 4, 3),), (grid,))
    program = AtomProgram((AtomStep((Store(grid, ((0, site.id),), duration_us=1),)),))

    report = validate_result(
        _result(program, entry), entry_state=entry, device=_device(site)
    )

    assert report["failure"] is not None
    assert report["failure"]["kind"] == "STORE"
    assert report["failure"]["rule"] == "active_trap"


def test_parallel_actions_are_checked_against_the_same_entry_layout() -> None:
    site = SLMSite("source", Position(10, 10))
    entry = AtomLayout((_stored(0, site),))
    loaded = _grid(((4, 10),), ((3, 10),))
    moved = _grid(((4, 20),), ((3, 10),))
    program = AtomProgram(
        (
            AtomStep(
                (
                    Load(loaded, ((0, 4, 3),), duration_us=1),
                    Move(moved, ((0, Position(10, 20)),), duration_us=10),
                )
            ),
        )
    )

    report = validate_result(
        _result(program, entry), entry_state=entry, device=_device(site)
    )

    assert report["failure"] is not None
    assert report["failure"]["action"] == 1
    assert report["failure"]["rule"] == "line_identity"


def test_valid_load_move_store_sequence_replays_to_recorded_exit() -> None:
    source = SLMSite("source", Position(10, 10))
    target = SLMSite("target", Position(10, 20))
    entry = AtomLayout((_stored(0, source),), qubit_to_atom=(0,))
    exit_state = AtomLayout((_stored(0, target),), qubit_to_atom=(0,))
    loaded = _grid(((4, 10),), ((3, 10),))
    moved = _grid(((4, 20),), ((3, 10),))
    program = AtomProgram(
        (
            AtomStep((Load(loaded, ((0, 4, 3),), duration_us=1),)),
            AtomStep((Move(moved, ((0, target.position),), duration_us=10),)),
            AtomStep((Store(AODGrid("transport"), ((0, target.id),), duration_us=1),)),
        )
    )

    assert validate_result(
        _result(program, exit_state), entry_state=entry, device=_device(source, target)
    ) == {"ok": True, "failure": None}


def test_emitted_operation_missing_from_source_ids_reports_coverage() -> None:
    entry = AtomLayout(())
    result = _result(AtomProgram(), entry, (CompiledOperation(7, "emitted", 0, 1),))

    assert validate_result(result, entry_state=entry, device=_device()) == {
        "ok": False,
        "failure": {
            "step": None,
            "action": None,
            "kind": "COVERAGE",
            "rule": "missing_source",
            "message": "Emitted operation 7 has no action source reference.",
        },
    }


def test_skipped_operation_reports_its_first_source_action() -> None:
    entry = AtomLayout(())
    action = Rotation("local", (), "X", 0.5, duration_us=1, source_ids=(7,))
    result = _result(
        AtomProgram((AtomStep((Wait(duration_us=1), action)),)),
        entry,
        (CompiledOperation(7, "skipped", 0, 0),),
    )

    assert validate_result(result, entry_state=entry, device=_device())["failure"] == {
        "step": 0,
        "action": 1,
        "kind": "ROT",
        "rule": "skipped_source",
        "message": "Skipped operation 7 appears in action source_ids.",
    }


def test_coverage_uses_operation_ids_instead_of_measurement_ids() -> None:
    site = SLMSite("source", Position(10, 10))
    entry = AtomLayout((_stored(0, site),))
    action = Measure(
        "readout", ((0, ResultRef(3)),), True, duration_us=1, source_ids=(9,)
    )
    result = _result(
        AtomProgram((AtomStep((action,)),)),
        entry,
        (CompiledOperation(9, "emitted", 0, 1),),
    )

    assert validate_result(result, entry_state=entry, device=_device(site))["ok"]


def test_global_phase_metadata_does_not_need_an_action_reference() -> None:
    entry = AtomLayout(())
    result = _result(
        AtomProgram(global_phase=0.25),
        entry,
        (CompiledOperation(0, "metadata", 0, 0),),
    )

    assert validate_result(result, entry_state=entry, device=_device())["ok"]


def test_exit_mismatch_is_a_whole_result_failure() -> None:
    first = SLMSite("first", Position(10, 10))
    second = SLMSite("second", Position(20, 20))
    entry = AtomLayout((_stored(0, first),))
    exit_state = AtomLayout((_stored(0, second),))

    report = validate_result(
        _result(AtomProgram(), exit_state),
        entry_state=entry,
        device=_device(first, second),
    )

    assert report["failure"] == {
        "step": None,
        "action": None,
        "kind": "RESULT",
        "rule": "exit_state_mismatch",
        "message": "Replayed layout does not equal the recorded exit state.",
    }


def test_invalid_entry_layout_is_reported_before_actions() -> None:
    site = SLMSite("source", Position(10, 10))
    atom = replace(_stored(0, site), position=Position(20, 20))
    entry = AtomLayout((atom,))

    report = validate_result(
        _result(AtomProgram(), entry), entry_state=entry, device=_device(site)
    )

    assert report["failure"] == {
        "step": None,
        "action": None,
        "kind": "ENTRY",
        "rule": "invalid_entry",
        "message": "Atom 0 position does not match its holder.",
    }


def test_unsupported_destructive_replay_reports_the_measurement_location() -> None:
    site = SLMSite("source", Position(10, 10))
    entry = AtomLayout((_stored(0, site),))
    action = Measure("readout", ((0, ResultRef(3)),), False, duration_us=1)
    result = _result(AtomProgram((AtomStep((action,)),)), entry)

    report = validate_result(result, entry_state=entry, device=_device(site))

    assert report["failure"] == {
        "step": 0,
        "action": 0,
        "kind": "M",
        "rule": "unsupported_replay",
        "message": "Replay currently supports retained readout only.",
    }


def test_empty_program_and_layout_pass() -> None:
    entry = AtomLayout(())

    assert validate_result(
        _result(AtomProgram(), entry), entry_state=entry, device=_device()
    ) == {"ok": True, "failure": None}
