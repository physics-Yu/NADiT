"""Independent action checks bind illumination and addressing to the source DAG."""

from dataclasses import replace

import pytest

from examples.viewer_cases import compile_feedback, compile_static
from nadit_physical_compiler.atom_ir import (
    AtomAction,
    AtomStep,
    CZPulse,
    GlobalRY,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
)
from nadit_physical_compiler.capabilities import (
    CZCapability,
    GlobalControl,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.coverage import RegionSet
from nadit_physical_compiler.device import Device, Position, Region, Timing, Zone
from nadit_physical_compiler.physical_ir import (
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.semantic_actions import check_action_semantics
from nadit_physical_compiler.state import (
    COORDINATE_TOLERANCE,
    AODGrid,
    AODHolder,
    AtomLayout,
    AtomState,
    SLMHolder,
)


def _device() -> Device:
    return Device(
        "semantic",
        (
            Zone(
                "all",
                Region(-100, -100, 100, 100),
                ("cz", "rotate", "reset", "measure"),
            ),
        ),
        (),
        (),
        Timing(1, 1, 1),
        cz=CZCapability("cz", 4.6, 1),
        local=LocalControl("local", 1, 1),
        readout=ReadoutCapability("readout", 1, min_spacing_um=5),
        global_control=GlobalControl("global", 1),
    )


def _layout(*coordinates: tuple[float, float]) -> AtomLayout:
    atoms = tuple(
        AtomState(7 + 3 * q, Position(*xy), SLMHolder(f"site_{q}"))
        for q, xy in enumerate(coordinates)
    )
    return AtomLayout(atoms, qubit_to_atom=tuple(atom.id for atom in atoms))


def _dag(*operations: tuple[str, tuple[int, ...], tuple[float, ...]]) -> PhysicalDAG:
    return PhysicalDAG(
        8,
        8,
        tuple(
            PhysicalOperation(index, name, qubits, (), params, source_id=20 + index)
            for index, (name, qubits, params) in enumerate(operations)
        ),
        (),
    )


def _rules(
    dag: PhysicalDAG,
    layout: AtomLayout,
    action: AtomAction,
    *,
    device: Device | None = None,
    step: AtomStep | None = None,
) -> list[str | None]:
    return [
        check.rule
        for check in check_action_semantics(
            dag, device or _device(), layout, action, step or AtomStep((action,))
        )
    ]


def _pulse(coverage: Region | RegionSet | tuple[int, ...], *sources: int) -> CZPulse:
    return CZPulse("cz", coverage, duration_us=1, source_ids=sources)


@pytest.mark.parametrize(
    "coverage",
    [
        Region(-1, -1, 3, 1),
        (7, 10),
        RegionSet((Region(-1, -1, 3, 1), Region(50, 50, 51, 51))),
    ],
)
def test_valid_pair_coverage(coverage: Region | RegionSet | tuple[int, ...]) -> None:
    assert (
        _rules(_dag(("cz", (0, 1), ())), _layout((0, 0), (2, 0)), _pulse(coverage, 0))
        == []
    )


def test_empty_illumination_reports_missing_interaction_first() -> None:
    assert _rules(
        _dag(("cz", (0, 1), ())),
        _layout((0, 0), (2, 0)),
        _pulse(Region(20, 20, 30, 30), 0),
    ) == ["missing_interaction"]


def test_illuminated_spectator_without_extra_edge() -> None:
    assert _rules(
        _dag(("cz", (0, 1), ())),
        _layout((0, 0), (2, 0), (20, 0)),
        _pulse(Region(-1, -1, 21, 1), 0),
    ) == ["illuminated_spectator"]


def test_cross_pair_edges_are_unintended_interactions() -> None:
    rules = _rules(
        _dag(("cz", (0, 1), ()), ("cz", (2, 3), ())),
        _layout((0, 0), (2, 0), (5, 0), (7, 0)),
        _pulse(Region(-1, -1, 8, 1), 0, 1),
    )
    assert "unintended_interaction" in rules
    assert "illuminated_spectator" not in rules


def test_holder_pairs_are_unordered_and_enforced() -> None:
    layout = _layout((0, 0), (2, 0))
    layout = replace(
        layout,
        atoms=(
            layout.atoms[0],
            replace(layout.atoms[1], holder=AODHolder("transport", 0, 0)),
        ),
    )
    dag, pulse = _dag(("cz", (0, 1), ())), _pulse((7, 10), 0)
    device = replace(_device(), cz=CZCapability("cz", 4.6, 1, (("aod", "slm"),)))
    assert _rules(dag, layout, pulse, device=device) == []
    device = replace(device, cz=CZCapability("cz", 4.6, 1, (("slm", "slm"),)))
    assert _rules(dag, layout, pulse, device=device) == ["holder_pair"]


def test_cz_pair_requires_one_permitted_zone_containing_both_atoms() -> None:
    device = replace(
        _device(),
        zones=(
            Zone("left", Region(-1, -1, 1, 1), ("cz",)),
            Zone("right", Region(1, -1, 3, 1), ("cz",)),
        ),
    )
    assert _rules(
        _dag(("cz", (0, 1), ())),
        _layout((0, 0), (2, 0)),
        _pulse((7, 10), 0),
        device=device,
    ) == ["zone_capability"]


@pytest.mark.parametrize(
    "coverage",
    [
        Region(-1, -1, 3, 1),
        (7, 10),
        RegionSet((Region(-1, -1, 3, 1), Region(40, 40, 41, 41))),
    ],
)
def test_movement_segment_crossing_pulse_is_rejected(
    coverage: Region | RegionSet | tuple[int, ...],
) -> None:
    layout = _layout((0, 0), (2, 0), (0, -5))
    pulse = _pulse(coverage, 0)
    move = Move(AODGrid("transport"), ((13, Position(0, 5)),), duration_us=2)
    assert _rules(
        _dag(("cz", (0, 1), ())), layout, pulse, step=AtomStep((move, pulse))
    ) == ["moving_in_pulse"]


def test_region_set_gap_is_not_illuminated_during_movement() -> None:
    layout = _layout((0, 0), (2, 0), (10, -5))
    pulse = _pulse(RegionSet((Region(-1, -1, 3, 1), Region(20, -1, 21, 1))), 0)
    move = Move(AODGrid("transport"), ((13, Position(10, 5)),), duration_us=2)
    assert (
        _rules(_dag(("cz", (0, 1), ())), layout, pulse, step=AtomStep((pulse, move)))
        == []
    )


def test_geometry_uses_shared_coordinate_tolerance() -> None:
    layout = _layout((0, 0), (4.6 + COORDINATE_TOLERANCE / 2, 0))
    assert (
        _rules(_dag(("cz", (0, 1), ())), layout, _pulse(Region(0, 0, 4.6, 0), 0)) == []
    )


@pytest.mark.parametrize(
    ("name", "params", "action"),
    [
        (
            "rx",
            (0.5,),
            Rotation("local", (7,), "X", 0.5, duration_us=1, source_ids=(0,)),
        ),
        (
            "u",
            (0.1, 0.2, 0.3),
            LocalU("local", (7,), 0.1, 0.2, 0.3, duration_us=1, source_ids=(0,)),
        ),
        ("reset", (), Reset("local", (7,), duration_us=1, source_ids=(0,))),
        (
            "global_ry",
            (0.5,),
            GlobalRY(
                "global", Region(-1, -1, 1, 1), 0.5, duration_us=1, source_ids=(0,)
            ),
        ),
        (
            "measure",
            (),
            Measure(
                "readout", ((7, ResultRef(20)),), True, duration_us=1, source_ids=(0,)
            ),
        ),
    ],
)
def test_addressed_operations_match_source(
    name: str,
    params: tuple[float, ...],
    action: AtomAction,
) -> None:
    assert _rules(_dag((name, (0,), params)), _layout((0, 0)), action) == []


def test_rotation_requires_permitted_zone_and_holder() -> None:
    device = replace(
        _device(),
        zones=(Zone("readout", Region(-1, -1, 1, 1), ("measure",)),),
        local=LocalControl("local", 1, 1, holders=("aod",)),
    )
    action = Rotation("local", (7,), "X", 0.5, duration_us=1, source_ids=(0,))
    assert _rules(
        _dag(("rx", (0,), (0.5,))), _layout((0, 0)), action, device=device
    ) == ["zone_capability", "holder"]


@pytest.mark.parametrize(
    "action",
    [
        Rotation("local", (10,), "X", 0.5, duration_us=1, source_ids=(0,)),
        Rotation("local", (7,), "Z", 0.5, duration_us=1, source_ids=(0,)),
        Rotation("local", (7,), "X", 0.7, duration_us=1, source_ids=(0,)),
        Rotation("wrong", (7,), "X", 0.5, duration_us=1, source_ids=(0,)),
        Rotation("local", (7, 7), "X", 0.5, duration_us=1, source_ids=(0,)),
        Rotation("local", (7,), "X", 0.5, duration_us=1, source_ids=(99,)),
    ],
)
def test_wrong_address_gate_or_channel_is_rejected(action: AtomAction) -> None:
    assert "addressing" in _rules(
        _dag(("rx", (0,), (0.5,))), _layout((0, 0), (10, 0)), action
    )


def test_bundled_rotation_sources_are_supported() -> None:
    action = Rotation("local", (7, 10), "X", 0.5, duration_us=1, source_ids=(1, 0))
    dag = _dag(("rx", (0,), (0.5,)), ("rx", (1,), (0.5,)))
    assert _rules(dag, _layout((0, 0), (10, 0)), action) == []


@pytest.mark.parametrize("coverage", [(7, 99), (7, 7, 10)])
def test_cz_unknown_or_repeated_address_is_reported(
    coverage: tuple[int, ...],
) -> None:
    rules = _rules(
        _dag(("cz", (0, 1), ())), _layout((0, 0), (2, 0)), _pulse(coverage, 0)
    )
    assert "addressing" in rules


def test_unknown_local_atom_is_reported_without_stopping_other_checks() -> None:
    action = Rotation("local", (99,), "X", 0.5, duration_us=1, source_ids=(0,))
    assert "addressing" in _rules(_dag(("rx", (0,), (0.5,))), _layout((0, 0)), action)


@pytest.mark.parametrize(
    "action",
    [
        _pulse((7, 10), 0),
        Rotation("local", (7,), "X", 0.5, duration_us=1, source_ids=(0,)),
    ],
)
def test_incomplete_qubit_mapping_reports_addressing(action: AtomAction) -> None:
    layout = replace(_layout((0, 0), (2, 0)), qubit_to_atom=())
    dag = (
        _dag(("cz", (0, 1), ()))
        if isinstance(action, CZPulse)
        else _dag(("rx", (0,), (0.5,)))
    )
    assert "addressing" in _rules(dag, layout, action)


def test_malformed_cz_arity_reports_addressing() -> None:
    assert "addressing" in _rules(
        _dag(("cz", (0, 1, 1), ())), _layout((0, 0), (2, 0)), _pulse((7, 10), 0)
    )


def test_readout_retention_matches_capability() -> None:
    action = Measure(
        "readout", ((7, ResultRef(20)),), False, duration_us=1, source_ids=(0,)
    )
    assert "addressing" in _rules(_dag(("measure", (0,), ())), _layout((0, 0)), action)


@pytest.mark.parametrize(
    ("start", "end", "intersects"),
    [
        ((-1, -1), (2, 2), True),
        ((-1, 1), (1, -1), True),
        ((0.5, -1), (0.5, 2), True),
        ((-1, 2), (2, -1), True),
        ((-1, 4), (4, -1), False),
        ((2, -1), (2, 2), False),
        ((-1, 0.5), (-0.1, 0.5), False),
        ((0.5, 0.5), (0.5, 0.5), True),
    ],
)
def test_closed_segment_coverage(
    start: tuple[float, float],
    end: tuple[float, float],
    intersects: bool,
) -> None:
    from nadit_physical_compiler.semantic_geometry import intersects_segment

    assert (
        intersects_segment(Region(0, 0, 1, 1), Position(*start), Position(*end))
        is intersects
    )


def test_global_rotation_detects_illuminated_extra_atom() -> None:
    action = GlobalRY(
        "global", Region(-1, -1, 11, 1), 0.5, duration_us=1, source_ids=(0,)
    )
    assert _rules(
        _dag(("global_ry", (0,), (0.5,))), _layout((0, 0), (10, 0)), action
    ) == ["addressing"]


def test_swapped_measurement_binding_is_rejected() -> None:
    action = Measure(
        "readout",
        ((7, ResultRef(21)), (10, ResultRef(20))),
        True,
        duration_us=1,
        source_ids=(0, 1),
    )
    dag = _dag(("measure", (0,), ()), ("measure", (1,), ()))
    assert _rules(dag, _layout((0, 0), (10, 0)), action) == ["result_binding"]


@pytest.mark.parametrize("bundled", [False, True])
def test_readout_spacing_covers_all_concurrent_measure_actions(bundled: bool) -> None:
    first = Measure(
        "readout", ((7, ResultRef(20)),), True, duration_us=1, source_ids=(0,)
    )
    second = Measure(
        "readout", ((10, ResultRef(21)),), True, duration_us=1, source_ids=(1,)
    )
    actions = (
        (replace(first, results=first.results + second.results, source_ids=(0, 1)),)
        if bundled
        else (first, second)
    )
    step = AtomStep(actions)
    dag = _dag(("measure", (0,), ()), ("measure", (1,), ()))
    rules = [
        rule
        for action in actions
        for rule in _rules(dag, _layout((0, 0), (2, 0)), action, step=step)
    ]
    assert rules == ["readout_spacing"]


@pytest.mark.parametrize("bit", [0, 1])
def test_compiled_feedback_examples_pass_action_semantics(bit: int) -> None:
    example = compile_feedback(bit)
    layout = example.entry
    for step in example.result.program.steps:
        for action in step.actions:
            assert (
                check_action_semantics(
                    example.dag, example.device, layout, action, step
                )
                == ()
            )
        layout = apply_step(layout, step, example.device)


def test_compiled_parallel_example_passes_action_semantics() -> None:
    example = compile_static()
    layout = example.entry
    for step in example.result.program.steps:
        for action in step.actions:
            assert (
                check_action_semantics(
                    example.dag, example.device, layout, action, step
                )
                == ()
            )
        layout = apply_step(layout, step, example.device)


@pytest.mark.parametrize("point_addressing", [False, True])
def test_simultaneous_same_channel_pulses_include_cross_coverage_interactions(
    point_addressing: bool,
) -> None:
    layout = _layout((0, 0), (2, 0), (5, 0), (7, 0))
    first = _pulse((7, 10) if point_addressing else Region(-1, -1, 3, 1), 0)
    second = _pulse((13, 16) if point_addressing else Region(4, -1, 8, 1), 1)
    step = AtomStep((first, second))
    dag = _dag(("cz", (0, 1), ()), ("cz", (2, 3), ()))

    assert _rules(dag, layout, first, step=step) == []
    assert _rules(dag, layout, second, step=step) == ["unintended_interaction"]


def test_simultaneous_same_channel_separated_pairs_remain_valid() -> None:
    layout = _layout((0, 0), (2, 0), (10, 0), (12, 0))
    pulses = (_pulse((7, 10), 0), _pulse((13, 16), 1))
    step = AtomStep(pulses)
    dag = _dag(("cz", (0, 1), ()), ("cz", (2, 3), ()))

    assert [_rules(dag, layout, pulse, step=step) for pulse in pulses] == [[], []]


def test_cross_pulse_interaction_is_reported_once_at_each_later_pulse() -> None:
    layout = _layout((0, 0), (2, 0), (5, 0), (7, 0), (10, 0), (12, 0))
    pulses = (_pulse((7, 10), 0), _pulse((13, 16), 1), _pulse((19, 22), 2))
    step = AtomStep(pulses)
    dag = _dag(("cz", (0, 1), ()), ("cz", (2, 3), ()), ("cz", (4, 5), ()))

    checks = [
        check_action_semantics(dag, _device(), layout, pulse, step) for pulse in pulses
    ]

    assert [len(check) for check in checks] == [0, 1, 1]
    assert "(10, 13)" in checks[1][0].message
    assert "(16, 19)" in checks[2][0].message


def test_cross_pulse_graph_includes_overlapping_coverage() -> None:
    layout = _layout((0, 0), (2, 0), (4, 0))
    pulses = (_pulse((7, 10), 0), _pulse((10, 13), 1))
    step = AtomStep(pulses)
    dag = _dag(("cz", (0, 1), ()), ("cz", (1, 2), ()))

    assert _rules(dag, layout, pulses[1], step=step) == ["unintended_interaction"]
