"""Write NADiT's normalized event traces for the pinned shared fixture batches."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from nadit_physical_compiler.transport.staging import StageEvent, generate_stages
from reference.cases import ReferenceCase, load_cases


class NormalizedEvent(TypedDict):
    """A stage kind and sorted atom coordinates in micrometers."""

    kind: str
    positions: list[list[int | float]]


def normalize_events(events: Sequence[StageEvent]) -> list[NormalizedEvent]:
    """Serialize stage events using the same shape as the C++ trace adapter."""
    return [
        {
            "kind": event.kind,
            "positions": [
                [atom, position.x, position.y]
                for atom, position in sorted(event.positions)
            ],
        }
        for event in events
    ]


def candidate_traces(
    cases: Sequence[ReferenceCase],
) -> dict[str, list[NormalizedEvent]]:
    """Generate one supplied batch per fixture without changing router grouping."""
    return {
        case.name: normalize_events(
            generate_stages(
                case.moves,
                case.entry,
                {atom.id: atom for atom in case.target.atoms},
                case.geometry,
            )
        )
        for case in cases
    }


def main() -> None:
    """Write fixture-name-to-event traces for run_reference's candidate input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    traces = candidate_traces(load_cases())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(traces, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(traces)} candidate event traces to {args.output}.")


if __name__ == "__main__":
    main()
