# Measurement Feedback Validation

Date: 2026-10-20.

## Reproduce

From `modules/physical_compiler/`, run:

```text
uv run --locked pytest -q
uv run --locked python examples/feedback_study.py --seed 17 --measurement-latency 2
```

The study uses `execute`, the DAG feedback reference tester. It imports the
circuit once, executes a seeded case twice, changes reporting latency, and
enumerates every assignment to its nine measurement results. It prints a JSON
summary.

It explicitly selects the `h`, `x`, `cx`, `ccx`, `p` gate basis to preserve
the source-level operation-count oracle below. The frontend's default CZ/
rotation basis is tested separately, including cross-basis result identity.

## Input

The [fixture](../tests/fixtures/logical.qasm3) is a byte-for-byte copy of the
supplied circuit. Its SHA-256 is
`57a6613215c0b6118fcf21b9c491ef22cde0bfe93ca7f0c6cdecad7e90deb391`.

| Quantity | Value |
| --- | ---: |
| Quantum wires | 14 |
| Classical wires | 9 |
| Physical DAG nodes | 117 |
| Dependency edges | 174 |
| Measurements | 9 |
| Conditional phase operations | 36 |
| Unconditional operations | 81 |

The frontend retains the original branches. Each condition references the
particular measurement that wrote its input bit.

## Results

The repository baseline was 86 passing tests. The completed increment has
131 passing tests, including an exhaustive integration test over this fixture.
Ruff formatting/linting and strict mypy checks also pass.

With seed 17, measurement values in `b[0]` through `b[8]` order are
`[0, 1, 0, 0, 0, 0, 1, 1, 1]`. The run executes 91 operations and skips 26.
With unit operation durations and reporting latency 2, it finishes at abstract
time 43. Repeating the run gives an identical trace.

All 512 assignments to the nine measurement results pass the execution checks.
They produce 256 distinct sets of executed operations: `b[8]` is an output and
does not control a later operation. Every conditional operation is tested both
executed and skipped. Executed-operation counts range from 81 to 117, and
abstract completion times range from 40 to 48.

The integration test independently checks the number of executed operations:

```text
81 + 8*b[0] + 7*b[1] + 6*b[2] + 5*b[3]
   + 4*b[4] + 3*b[5] + 2*b[6] + b[7]
```

It also checks each dependency's completion before its successor starts and
each measurement result's availability before a condition reads it. Changing
reporting latency from 2 to 10 preserves the seeded values and selected
operations while increasing completion time.

## Interpretation

This validates QASM import, result binding, conditional execution, dependency
release, and reproducible feedback traces. Measurement values are assigned
test inputs, and time is measured in abstract units.

See [ADR 0004](../../../docs/decisions/0004-measurement-feedback.md) for the
reference tester and
[ADR 0005](../../../docs/decisions/0005-case-driven-physical-compilation.md)
for the next physical compiler increment.
