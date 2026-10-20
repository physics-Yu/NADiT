"""Adapt shared routing fixtures to NADiT without device admission callbacks."""

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nadit_physical_compiler.device import AOD, Position, Region, Timing
from nadit_physical_compiler.transport.relaxed_groups import route_relaxed
from nadit_physical_compiler.transport.relaxed_rules import RelaxedRules
from nadit_physical_compiler.transport.rules import MoveRequest

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class RelaxedReferenceCase:
    """One coordinate fixture with unrestricted line capacity and its threshold."""

    name: str
    moves: tuple[MoveRequest, ...]
    rules: RelaxedRules
    prefer_split: float


def load_cases(
    path: Path = HERE / "relaxed_fixtures.json",
) -> tuple[RelaxedReferenceCase, ...]:
    """Convert shared integer coordinates to the module's micrometre requests."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    scale = fixture["nm_per_um"]
    cases = []
    for case in fixture["cases"]:
        moves = tuple(
            MoveRequest(
                atom, Position(sx / scale, sy / scale), Position(tx / scale, ty / scale)
            )
            for atom, (sx, sy, tx, ty) in enumerate(case["movements"])
        )
        points = tuple(point for move in moves for point in (move.source, move.target))
        bounds = Region(
            min(point.x for point in points) - 1,
            min(point.y for point in points) - 1,
            max(point.x for point in points) + 1,
            max(point.y for point in points) + 1,
        )
        rules = RelaxedRules(AOD("reference", bounds, 1 / scale), Timing(10, 10, 1))
        cases.append(
            RelaxedReferenceCase(case["name"], moves, rules, case["prefer_split"])
        )
    return tuple(cases)


def candidate_results(
    cases: tuple[RelaxedReferenceCase, ...],
) -> dict[str, dict[str, Any]]:
    """Observe pair costs, strict initialization, and unfiltered relaxed output.

    Strict initialization is observed by rejecting every proposed merge.
    The relaxed call uses neither initial groups nor an admission callback.
    """
    results = {}
    for case in cases:
        pairs = []
        for index, first in enumerate(case.moves):
            for second in case.moves[index + 1 :]:
                compatibility = case.rules.compatibility(first, second)
                pairs.append(
                    {
                        "atoms": [first.atom_id, second.atom_id],
                        "status": compatibility.status,
                        "merge_cost": compatibility.merge_cost,
                    }
                )
        strict = route_relaxed(case.moves, case.rules, admit=lambda _: False)
        relaxed = route_relaxed(case.moves, case.rules, prefer_split=case.prefer_split)
        results[case.name] = {
            "pairs": pairs,
            "strict_groups": [
                sorted(move.atom_id for move in group) for group in strict
            ],
            "relaxed_groups": [
                sorted(move.atom_id for move in group) for group in relaxed
            ],
        }
    return results


def compare_result(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare pair classifications/costs and group membership/order.

    The C++ mover-order field is source evidence, not an exposed candidate API.
    Group order is compared; only member append order is normalized.

    Raises:
        ValueError: A pair, cost, or ordered grouping differs.
    """
    for field in ("strict_groups", "relaxed_groups"):
        if expected[field] != actual[field]:
            raise ValueError(f"{field}: {expected[field]} != {actual[field]}")
    if len(expected["pairs"]) != len(actual["pairs"]):
        raise ValueError("Pair counts differ")
    for left, right in zip(expected["pairs"], actual["pairs"], strict=True):
        if left["atoms"] != right["atoms"] or left["status"] != right["status"]:
            raise ValueError(f"Pair classification differs: {left} != {right}")
        a, b = left["merge_cost"], right["merge_cost"]
        if (a is None) != (b is None) or (
            a is not None and not math.isclose(a, b, abs_tol=1e-9, rel_tol=1e-12)
        ):
            raise ValueError(f"Pair merge cost differs: {left} != {right}")


def main() -> None:
    """Write candidate traces and optionally compare a generated C++ oracle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    results = candidate_results(load_cases())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    if args.expected is not None:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))["cases"]
        for name, result in results.items():
            compare_result(expected[name], result)
        print(f"All {len(results)} candidate routing results match original C++.")
    print(args.output)


if __name__ == "__main__":
    main()
