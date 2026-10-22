"""Compare batching kernels with the pinned upstream ENOLA source."""

import ast
from hashlib import sha256
from itertools import combinations, product
from pathlib import Path
from typing import Any

import pytest
from test_nonzoned import shared_device

from nadit_physical_compiler.device import Position, SLMSite
from nadit_physical_compiler.nonzoned.planning import Candidate, _Selector, conflict_map
from nadit_physical_compiler.transport.occupancy import Occupancy
from nadit_physical_compiler.transport.rules import MoveRequest, rules_for


@pytest.fixture
def upstream() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures/enola/router_mis.py.txt"
    raw = path.read_bytes()
    assert (
        sha256(raw).hexdigest()
        == "a75e92353a74b6f3597a33b214d358b0782de2fa60400362c5e1967646cecb82"
    )
    names = {"compatible_2D", "maximalis_solve_sort"}
    nodes = [
        n
        for n in ast.parse(raw).body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    assert {n.name for n in nodes} == names
    scope: dict[str, Any] = {}
    exec(compile(ast.Module(body=[*nodes], type_ignores=[]), str(path), "exec"), scope)
    return scope


def test_axis_compatibility_matches_upstream(upstream: dict[str, Any]) -> None:
    device, _ = shared_device()
    rules = rules_for(device.aods[0], device.timing)
    vectors = tuple(product((0, 10, 20), repeat=4))
    for a, b in combinations(vectors, 2):
        first = MoveRequest(0, Position(a[2], a[0]), Position(a[3], a[1]))
        second = MoveRequest(1, Position(b[2], b[0]), Position(b[3], b[1]))
        assert rules.compatible(first, second) == upstream["compatible_2D"](a, b)


@pytest.mark.parametrize(
    "pairs",
    [
        (((0, 0), (20, 0)), ((10, 0), (30, 0))),
        (((0, 0), (30, 0)), ((10, 0), (20, 0))),
        (((0, 0), (20, 10)), ((0, 10), (20, 0)), ((10, 0), (30, 10))),
    ],
)
def test_sorted_greedy_batches_match_upstream_without_extra_limits(
    upstream: dict[str, Any], pairs: tuple[tuple[tuple[int, int], tuple[int, int]], ...]
) -> None:
    device, _ = shared_device(2)
    rules = rules_for(device.aods[0], device.timing)
    ordered = sorted(
        pairs, key=lambda p: -((p[0][0] - p[1][0]) ** 2 + (p[0][1] - p[1][1]) ** 2)
    )
    candidates = tuple(
        Candidate(
            gate,
            MoveRequest(2 * gate + side, Position(*a), Position(*b)),
            SLMSite(f"t{gate}_{side}", Position(*b)),
        )
        for gate, pair in enumerate(ordered)
        for side, (a, b) in enumerate((pair, pair[::-1]))
    )
    selector = _Selector(
        candidates, conflict_map(candidates, rules), rules, Occupancy({})
    )
    pending = set(range(len(candidates)))
    while pending:
        ids = sorted(pending)
        vectors = [
            (
                candidates[i].move.source.y,
                candidates[i].move.target.y,
                candidates[i].move.source.x,
                candidates[i].move.target.x,
            )
            for i in ids
        ]
        edges = [
            (i, j)
            for i, j in combinations(range(len(ids)), 2)
            if not upstream["compatible_2D"](vectors[i], vectors[j])
        ]
        expected = tuple(
            ids[i] for i in upstream["maximalis_solve_sort"](len(ids), edges)
        )
        actual = selector.select(pending)
        assert actual == expected
        selected_gates = {candidates[i].operation_id for i in actual}
        pending = {
            i for i in pending if candidates[i].operation_id not in selected_gates
        }
