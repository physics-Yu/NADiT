"""Regression for the first changed A* decision in the graphstate-60 trace."""

import json
from pathlib import Path
from typing import Any

from nadit_physical_compiler.placement.scoring import _Node
from reference.placement_trace_candidate import make_search


def test_first_astar_choice_uses_source_float32_priority() -> None:
    path = (
        Path(__file__).resolve().parents[1] / "reference/placement_stage_fixtures.json"
    )
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["name"] == "graphstate_60_astar_gate_0")
    search, _ = make_search(case)
    children = list(search.expand(_Node()))

    # The actual C++ observer reproduces the uninstrumented first gate layout.
    # Float64 evaluation made these two priorities equal and selected option 13.
    assert search.heuristic(_Node()) == 133.3637237548828
    assert (search.cost(children[13]), search.heuristic(children[13])) == (
        9.147565841674805,
        162.0609893798828,
    )
    assert (search.cost(children[37]), search.heuristic(children[37])) == (
        8.95890998840332,
        162.2496337890625,
    )
    priorities = [search.cost(node) + search.heuristic(node) for node in children]
    assert [
        index for index, value in enumerate(priorities) if value == min(priorities)
    ] == [37]
