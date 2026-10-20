"""Device-admitted relaxed batches preserve active line order at every move."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_staged_reference import _audit

from nadit_physical_compiler.atom_ir import Move
from nadit_physical_compiler.contracts import AtomFragment
from nadit_physical_compiler.device import Region
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.state import AODHolder, AODLine
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport
from reference.cases import ReferenceCase, load_cases

REFERENCE = Path(__file__).parents[1] / "reference"
CASES = load_cases(REFERENCE / "relaxed_staging_fixtures.json")


def _wide(case: ReferenceCase) -> ReferenceCase:
    aod = case.device.aod("transport")
    bounds = aod.workspace
    workspace = Region(
        bounds.x_min - 20,
        bounds.y_min - 20,
        bounds.x_max + 20,
        bounds.y_max + 20,
    )
    return replace(
        case, device=replace(case.device, aods=(replace(aod, workspace=workspace),))
    )


def _order(lines: tuple[AODLine, ...]) -> tuple[int, ...]:
    return tuple(line.id for line in sorted(lines, key=lambda line: line.coordinate))


def _peak_carried(case: ReferenceCase, fragment: AtomFragment) -> int:
    layout = case.entry
    peak = 0
    for step in fragment.steps:
        for action in step.actions:
            if isinstance(action, Move):
                before = layout.grid(action.grid.aod_id)
                assert _order(before.rows) == _order(action.grid.rows)
                assert _order(before.columns) == _order(action.grid.columns)
        layout = apply_step(layout, step, case.device)
        peak = max(
            peak,
            sum(isinstance(atom.holder, AODHolder) for atom in layout.atoms),
        )
    return peak


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_public_relaxed_transport_merges_reversals_without_crossing_active_lines(
    case: ReferenceCase,
) -> None:
    case = _wide(case)
    relaxed = RelaxedTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )
    strict = StagedStrictTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    _audit(case, relaxed)
    _audit(case, strict)
    assert _peak_carried(case, relaxed) == 2
    assert _peak_carried(case, strict) == 1


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_insufficient_line_capacity_keeps_singleton_strict_batches(
    case: ReferenceCase,
) -> None:
    case = _wide(case)
    aod = case.device.aod("transport")
    limited = (
        replace(aod, max_columns=1)
        if case.name == "x_reversal"
        else replace(aod, max_rows=1)
    )
    case = replace(case, device=replace(case.device, aods=(limited,)))

    fragment = RelaxedTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    _audit(case, fragment)
    assert _peak_carried(case, fragment) == 1


def test_insufficient_staging_workspace_keeps_direct_strict_batches() -> None:
    case = next(case for case in CASES if case.name == "x_reversal")
    points = [point for move in case.moves for point in (move.source, move.target)]
    workspace = Region(
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )
    case = replace(
        case,
        device=replace(
            case.device,
            aods=(replace(case.device.aod("transport"), workspace=workspace),),
        ),
    )

    fragment = RelaxedTransport("transport", case.geometry).construct(
        case.entry, case.target, case.device
    )

    _audit(case, fragment)
    assert _peak_carried(case, fragment) == 1


def test_high_prefer_split_keeps_reference_strict_groups() -> None:
    case = _wide(next(case for case in CASES if case.name == "x_reversal"))

    fragment = RelaxedTransport(
        "transport", case.geometry, prefer_split=1_000_000
    ).construct(case.entry, case.target, case.device)

    _audit(case, fragment)
    assert _peak_carried(case, fragment) == 1
