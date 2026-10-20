"""Raw relaxed routing and staged atom traces match executed pinned C++."""

import json
from pathlib import Path

import pytest
from test_staged_reference import _audit

from nadit_physical_compiler.transport.rules import StrictRules
from nadit_physical_compiler.transport.staged_emission import emit_stages
from nadit_physical_compiler.transport.staging import generate_stages
from reference.cases import ReferenceCase
from reference.cases import load_cases as load_staging_cases
from reference.relaxed_candidate import (
    RelaxedReferenceCase,
    candidate_results,
    compare_result,
)
from reference.relaxed_candidate import load_cases as load_routing_cases
from reference.run_candidate import normalize_events

REFERENCE = Path(__file__).parents[1] / "reference"
ROUTING_CASES = load_routing_cases()
STAGING_CASES = load_staging_cases(REFERENCE / "relaxed_staging_fixtures.json")


@pytest.mark.parametrize("case", ROUTING_CASES, ids=lambda case: case.name)
def test_raw_relaxed_pair_costs_and_ordered_groups_match_cpp(
    case: RelaxedReferenceCase,
) -> None:
    expected = json.loads(
        (REFERENCE / "relaxed_expected.json").read_text(encoding="utf-8")
    )

    actual = candidate_results((case,))[case.name]

    compare_result(expected["cases"][case.name], actual)


@pytest.mark.parametrize("case", STAGING_CASES, ids=lambda case: case.name)
def test_raw_reversal_stages_match_cpp_and_have_legal_active_line_order(
    case: ReferenceCase,
) -> None:
    destinations = {atom.id: atom for atom in case.target.atoms}
    events = generate_stages(case.moves, case.entry, destinations, case.geometry)
    expected = json.loads(
        (REFERENCE / "relaxed_staging_expected.json").read_text(encoding="utf-8")
    )["cases"][case.name]
    actual = normalize_events(events)

    assert len(actual) == len(expected)
    for emitted, original in zip(actual, expected, strict=True):
        assert emitted["kind"] == original["kind"]
        assert len(emitted["positions"]) == len(original["positions"])
        for point, reference in zip(
            emitted["positions"], original["positions"], strict=True
        ):
            assert point[0] == reference[0]
            assert point[1:] == pytest.approx(reference[1:], rel=0, abs=1e-9)
    fragment = emit_stages(
        events,
        case.moves,
        case.entry,
        destinations,
        case.device,
        StrictRules(case.device.aod("transport"), case.device.timing),
    )
    _audit(case, fragment)
