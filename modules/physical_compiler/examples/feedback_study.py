"""Reproduce the feedback study on the supplied nine-result QASM3 circuit."""

import argparse
import json
from hashlib import sha256
from itertools import product
from pathlib import Path

from nadit_physical_compiler import FakeBackend, GateSet, execute, load_qasm


def main() -> None:
    """Run one seeded case and all measurement assignments; print a JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--measurement-latency", type=float, default=2.0)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "tests/fixtures/logical.qasm3"
    gate_set = GateSet(("h", "x", "cx", "ccx", "p"))
    dag = load_qasm(source, gate_set=gate_set)
    measurements = {
        result: node for node in dag.nodes if (result := node.result) is not None
    }
    conditions = [node for node in dag.nodes if node.condition is not None]
    trace = execute(
        dag, FakeBackend(args.seed), measurement_latency=args.measurement_latency
    )
    repeated = execute(
        dag, FakeBackend(args.seed), measurement_latency=args.measurement_latency
    )
    changed_timing = execute(
        dag,
        FakeBackend(args.seed),
        measurement_latency=args.measurement_latency + 1.0,
    )
    assert trace == repeated
    assert {event.result: event.value for event in trace.measurements} == {
        event.result: event.value for event in changed_timing.measurements
    }
    assert [event.status for event in trace.operations] == [
        event.status for event in changed_timing.operations
    ]

    counts: list[int] = []
    durations: list[float] = []
    selected_paths: set[tuple[int, ...]] = set()
    branch_coverage = {node.id: set[str]() for node in conditions}
    for bits in product((0, 1), repeat=len(measurements)):
        overrides = dict(zip(measurements, bits, strict=True))
        case = execute(
            dag,
            FakeBackend(overrides=overrides),
            measurement_latency=args.measurement_latency,
        )
        assert {event.result: event.value for event in case.measurements} == overrides
        for node, event in zip(dag.nodes, case.operations, strict=True):
            expected = (
                node.condition is None
                or overrides[node.condition.result] == node.condition.value
            )
            assert (event.status == "executed") == expected
            if node.condition is not None:
                branch_coverage[node.id].add(event.status)
        counts.append(sum(event.status == "executed" for event in case.operations))
        durations.append(case.duration)
        selected_paths.add(
            tuple(
                event.operation_id
                for event in case.operations
                if event.status == "executed"
            )
        )

    assert all(states == {"executed", "skipped"} for states in branch_coverage.values())
    report = {
        "source": source.name,
        "sha256": sha256(source.read_bytes()).hexdigest(),
        "scope": "Feedback control only; durations use abstract units.",
        "circuit": {
            "qubits": dag.num_qubits,
            "clbits": dag.num_clbits,
            "nodes": len(dag.nodes),
            "edges": len(dag.edges),
            "measurements": len(measurements),
            "conditional_operations": len(conditions),
        },
        "configuration": {
            "gate_set": gate_set.names,
            "seed": args.seed,
            "measurement_latency": args.measurement_latency,
            "operation_duration": 1.0,
        },
        "seeded_case": {
            "results": [
                {
                    "measurement_id": event.result.measurement_id,
                    "clbit": measurements[event.result].clbits[0],
                    "value": event.value,
                    "available_at": event.available_at,
                }
                for event in trace.measurements
            ],
            "executed": sum(event.status == "executed" for event in trace.operations),
            "skipped": sum(event.status == "skipped" for event in trace.operations),
            "duration": trace.duration,
            "repeat_identical": trace == repeated,
            "timing_change_preserves_choices": True,
        },
        "exhaustive_control_cases": {
            "assignments": len(counts),
            "distinct_selected_paths": len(selected_paths),
            "operations_executed_range": [min(counts), max(counts)],
            "duration_range": [min(durations), max(durations)],
            "conditions_covered_both_ways": len(branch_coverage),
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
