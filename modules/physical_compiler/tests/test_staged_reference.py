"""Compare staged atom events with executed C++ and check explicit grid legality."""

import json
from math import hypot
from pathlib import Path

import pytest

from nadit_physical_compiler.atom_ir import Load, Move, Store
from nadit_physical_compiler.contracts import AtomFragment
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import validate_layout
from nadit_physical_compiler.transport import StagedStrictTransport
from nadit_physical_compiler.transport.rules import StrictRules
from nadit_physical_compiler.transport.staged_emission import emit_stages
from nadit_physical_compiler.transport.staging import generate_stages
from reference.cases import ReferenceCase, load_cases
from reference.run_candidate import candidate_traces, normalize_events

REFERENCE = Path(__file__).parents[1] / "reference"
CASES = load_cases()


def _audit(case: ReferenceCase, fragment: AtomFragment) -> None:
    layout = case.entry
    moving = {move.atom_id for move in case.moves}
    stationary = {atom.id: atom for atom in layout.atoms if atom.id not in moving}
    validate_layout(case.device, layout)
    for step in fragment.steps:
        for action in step.actions:
            if isinstance(action, Load):
                checked = check_load(
                    case.device,
                    layout,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
                assert action.duration_us == case.device.timing.load_us
            elif isinstance(action, Move):
                checked = check_move(
                    case.device, layout, action.grid, dict(action.targets)
                )
                distances = (
                    hypot(
                        target.x - layout.atom(atom).position.x,
                        target.y - layout.atom(atom).position.y,
                    )
                    for atom, target in action.targets
                )
                assert action.duration_us == pytest.approx(
                    case.device.timing.move_duration(max(distances, default=0))
                )
            else:
                assert isinstance(action, Store)
                checked = check_store(
                    case.device, layout, action.grid, dict(action.bindings)
                )
                assert action.duration_us == case.device.timing.store_us
            assert checked.ok, (case.name, checked.message)
        layout = apply_step(layout, step, case.device)
        validate_layout(case.device, layout)
        for atom_id, atom in stationary.items():
            assert layout.atom(atom_id) == atom
    assert layout == fragment.exit_layout == case.target
    assert fragment.completed_operation_ids == ()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_supplied_batch_matches_executed_cpp_and_emits_legal_atom_ir(
    case: ReferenceCase,
) -> None:
    destinations = {atom.id: atom for atom in case.target.atoms}
    events = generate_stages(case.moves, case.entry, destinations, case.geometry)
    actual = normalize_events(events)
    oracle = json.loads((REFERENCE / "expected.json").read_text(encoding="utf-8"))
    expected = oracle["cases"][case.name]

    assert len(actual) == len(expected)
    for emitted, reference in zip(actual, expected, strict=True):
        assert emitted["kind"] == reference["kind"]
        assert len(emitted["positions"]) == len(reference["positions"])
        for point, original in zip(
            emitted["positions"], reference["positions"], strict=True
        ):
            assert point[0] == original[0]
            assert point[1:] == pytest.approx(original[1:], rel=0, abs=1e-9)
    fragment = emit_stages(
        events,
        case.moves,
        case.entry,
        destinations,
        case.device,
        StrictRules(case.device.aod("transport"), case.device.timing),
    )
    _audit(case, fragment)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_complete_staged_router_selects_legal_batches_for_reference_endpoints(
    case: ReferenceCase,
) -> None:
    fragment = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    _audit(case, fragment)


def test_candidate_json_covers_every_shared_fixture() -> None:
    traces = candidate_traces(CASES)

    assert set(traces) == {case.name for case in CASES}
    assert json.loads(json.dumps(traces)) == traces
    assert all(events for events in traces.values())
