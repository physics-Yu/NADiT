"""Compare queue removals against executed original C++ heap operations."""

import json
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.placement.queue import _BoundedQueue

REFERENCE = Path(__file__).resolve().parents[1] / "reference"
CASES: list[dict[str, Any]] = json.loads(
    (REFERENCE / "queue_fixtures.json").read_text(encoding="utf-8")
)
EXPECTED = json.loads((REFERENCE / "queue_expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_queue_matches_original_cpp(case: dict[str, Any]) -> None:
    queue = _BoundedQueue[int](case["capacity"])
    popped: list[list[float | int]] = []
    for operation in case["operations"]:
        if operation[0] == "p":
            queue.push(operation[1], operation[2])
        elif operation[0] == "o":
            priority, node = queue.pop(shrink=bool(operation[1]))
            popped.append([priority, node])
        else:
            while queue:
                priority, node = queue.pop()
                popped.append([priority, node])
    assert {"popped": popped, "empty": not bool(queue)} == EXPECTED["cases"][
        case["name"]
    ]


def test_original_equal_priority_order_is_not_fifo() -> None:
    # Executed from the pinned BoundedPriorityQueue, not inferred from Python.
    assert EXPECTED["cases"]["equal_priorities"]["popped"] == [
        [1, 0],
        [1, 3],
        [1, 2],
        [1, 1],
    ]
