"""Exercise source transport templates beyond the former admission shortcuts."""

import json
from dataclasses import replace
from itertools import permutations
from pathlib import Path

import pytest
from test_relaxed_transport import _peak_carried
from test_staged_reference import _audit

from nadit_physical_compiler.atom_ir import AtomProgram, Move
from nadit_physical_compiler.contracts import AtomFragment
from nadit_physical_compiler.device import Position, Region
from nadit_physical_compiler.motion import check_move
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport
from nadit_physical_compiler.transport.rules import StrictRules
from nadit_physical_compiler.transport.staged_emission import emit_stages
from nadit_physical_compiler.transport.staging import StageEvent, generate_stages
from reference.cases import ReferenceCase, load_cases

REFERENCE = Path(__file__).parents[1] / "reference"
type SiteRef = tuple[int, int, int]


def _case(
    tmp_path: Path,
    name: str,
    sources: tuple[SiteRef, ...],
    targets: tuple[SiteRef, ...],
    stationary: tuple[SiteRef, ...] = (),
) -> ReferenceCase:
    data = {
        "units": "nm",
        "nm_per_um": 1000,
        "slms": [
            {
                "kind": "storage",
                "origin": [0, 0],
                "pitch": [5000, 5000],
                "rows": 6,
                "columns": 6,
            },
            {
                "kind": "left",
                "origin": [0, 120000],
                "pitch": [8000, 10000],
                "rows": 6,
                "columns": 6,
            },
            {
                "kind": "right",
                "origin": [2000, 120000],
                "pitch": [8000, 10000],
                "rows": 6,
                "columns": 6,
            },
        ],
        "cases": [
            {
                "name": name,
                "source_sites": sources,
                "target_sites": targets,
                "stationary_sites": stationary,
            }
        ],
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    case = load_cases(path)[0]
    aod = replace(case.device.aod("transport"), workspace=Region(-100, -100, 150, 240))
    return replace(case, device=replace(case.device, aods=(aod,)))


def _reversed_case(tmp_path: Path, columns: int) -> ReferenceCase:
    return _case(
        tmp_path,
        f"reversed_gate_{columns}",
        tuple((0, 0, column) for column in range(columns)),
        tuple((1, 0, column) for column in reversed(range(columns))),
    )


def _assert_complete(case: ReferenceCase, fragment: AtomFragment) -> int:
    _audit(case, fragment)
    assert replay(AtomProgram(fragment.steps), case.entry, case.device) == case.target
    return _peak_carried(case, fragment)


@pytest.mark.parametrize("columns", [3, 4, 6])
def test_reversed_gate_columns_are_not_split_by_an_admission_count(
    tmp_path: Path, columns: int
) -> None:
    case = _reversed_case(tmp_path, columns)

    fragment = RelaxedTransport("transport", case.geometry, prefer_split=0).construct(
        case.entry, case.target, case.device
    )

    assert _assert_complete(case, fragment) == columns


def test_three_reversed_columns_fit_the_actual_eight_micrometer_margin(
    tmp_path: Path,
) -> None:
    case = _reversed_case(tmp_path, 3)
    aod = replace(case.device.aod("transport"), workspace=Region(-8, -10, 58, 190))
    case = replace(case, device=replace(case.device, aods=(aod,)))

    fragment = RelaxedTransport("transport", case.geometry, prefer_split=0).construct(
        case.entry, case.target, case.device
    )

    assert _assert_complete(case, fragment) == 3
    reached_x = [
        point.x
        for step in fragment.steps
        for action in step.actions
        if isinstance(action, Move)
        for _, point in action.targets
    ]
    assert min(reached_x) == -6


@pytest.mark.parametrize("columns", [3, 4])
def test_sparse_strict_columns_do_not_require_cartesian_completion(
    tmp_path: Path, columns: int
) -> None:
    case = _case(
        tmp_path,
        f"sparse_strict_{columns}",
        tuple((0, column, column) for column in range(columns)),
        tuple((1, column, column) for column in range(columns)),
    )

    fragment = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    assert _assert_complete(case, fragment) == columns


def test_partial_reversal_keeps_all_three_atoms_clear_of_a_retained_partner(
    tmp_path: Path,
) -> None:
    case = _case(
        tmp_path,
        "partial_reversal_retained_partner",
        ((0, 0, 0), (0, 0, 1), (0, 0, 2)),
        ((1, 0, 0), (2, 0, 1), (2, 0, 0)),
        ((2, 0, 2),),
    )

    fragment = RelaxedTransport("transport", case.geometry, prefer_split=0).construct(
        case.entry, case.target, case.device
    )

    assert _assert_complete(case, fragment) == 3


@pytest.mark.parametrize("columns", tuple(permutations(range(6), 3)))
def test_three_column_templates_preserve_every_unselected_gate_partner(
    tmp_path: Path, columns: tuple[int, int, int]
) -> None:
    def site(column: int) -> SiteRef:
        return (1 + column % 2, 0, column // 2)

    case = _case(
        tmp_path,
        "three_column_partner_matrix",
        ((0, 0, 0), (0, 0, 1), (0, 0, 2)),
        tuple(site(column) for column in columns),
        tuple(site(column) for column in range(6) if column not in columns),
    )
    destinations = {atom.id: atom for atom in case.target.atoms}
    events = generate_stages(case.moves, case.entry, destinations, case.geometry)
    fragment = emit_stages(
        events,
        case.moves,
        case.entry,
        destinations,
        case.device,
        StrictRules(case.device.aod("transport"), case.device.timing),
    )

    assert _assert_complete(case, fragment) == 3


def test_recorded_source_collision_is_corrected_with_the_same_six_atom_batch(
    tmp_path: Path,
) -> None:
    record = json.loads(
        (REFERENCE / "unsafe_sparse_gate.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "recorded_sparse_gate.json"
    path.write_text(
        json.dumps(
            {
                "nm_per_um": record["nm_per_um"],
                "slms": record["slms"],
                "cases": [record["case"]],
            }
        ),
        encoding="utf-8",
    )
    case = load_cases(path)[0]
    destinations = {atom.id: atom for atom in case.target.atoms}
    rules = StrictRules(case.device.aod("transport"), case.device.timing)
    recorded_events = tuple(
        StageEvent(
            event["kind"],
            tuple((atom, Position(x, y)) for atom, x, y in event["positions"]),
        )
        for event in record["reference_run"]["events"]
    )
    original = emit_stages(
        recorded_events, case.moves, case.entry, destinations, case.device, rules
    )
    event_index = record["reference_run"]["collisions"][0]["event_index"]
    current = case.entry
    for step in original.steps[:event_index]:
        current = apply_step(current, step, case.device)
    collision = original.steps[event_index].actions[0]
    assert isinstance(collision, Move)
    checked = check_move(case.device, current, collision.grid, dict(collision.targets))
    assert checked.rule == "occupied"

    corrected_events = generate_stages(
        case.moves, case.entry, destinations, case.geometry
    )
    corrected = emit_stages(
        corrected_events, case.moves, case.entry, destinations, case.device, rules
    )
    assert _assert_complete(case, corrected) == 6
    compiled = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )
    assert _assert_complete(case, compiled) == 6


@pytest.mark.parametrize("limit", ["capacity", "spacing", "workspace"])
def test_removing_admission_shortcuts_keeps_real_device_limits(
    tmp_path: Path, limit: str
) -> None:
    case = _reversed_case(tmp_path, 6)
    aod = case.device.aod("transport")
    if limit == "capacity":
        aod = replace(aod, max_columns=2)
    elif limit == "spacing":
        aod = replace(aod, min_spacing=3)
    else:
        aod = replace(aod, workspace=Region(0, 0, 40, 120))
    case = replace(case, device=replace(case.device, aods=(aod,)))

    fragment = RelaxedTransport("transport", case.geometry, prefer_split=0).construct(
        case.entry, case.target, case.device
    )

    peak = _assert_complete(case, fragment)
    if limit == "capacity":
        assert peak <= 2
