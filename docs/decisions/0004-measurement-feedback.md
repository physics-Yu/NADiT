# 0004: Measurement Feedback

Status: Implemented; accepted on 2026-10-20. `execute` is a feedback reference
tester. [ADR 0005](0005-case-driven-physical-compilation.md) defines the subsequent
compiler-owned progress model.

## Context

The physical frontend needs to preserve measurement feedback and exercise a
complete result-to-condition path. The initial
[static frontend decision](0003-qasm-physical-dag-frontend.md) deferred dynamic
circuits. This increment extends that boundary with fixed conditional bodies
and a fake feedback reference tester.

Result identity and conditional execution should also be reusable by later
logical-level work.

## Decision

Add `ResultRef(measurement_id)` and `Condition(result, value)` to the IR, and an
optional `condition` on `PhysicalOperation`. The condition value is one bit;
its default is `1`.

A result reference identifies the producing measurement operation, not a
mutable classical register slot. Import binds a condition to the latest
preceding measurement of its source bit. Later measurements that write the
same bit produce distinct results and do not rebind existing conditions.

Support the following QASM subset:

- OpenQASM 2.0: equality of a one-bit classical register with `0` or `1`.
- OpenQASM 3.0: a measured bit, its boolean negation, or equality with `true`
  or `false`, with an optional `else`. Bit comparisons use boolean literals
  because the importer does not accept bit-to-integer comparisons.
- Fixed conditional bodies containing gates, reset, and barrier. Same-source
  custom gates can expand inside these bodies.

Nested conditions, loops, measurements inside branches, multi-bit conditions,
and conditions on bits without a preceding measurement remain unsupported.
Standard-include restrictions and pinned dependencies from ADR 0003 remain
unchanged.

Flatten each branch into operation nodes with a shared condition; `else` nodes
carry the opposite value. Preserve branch-local global phase as a conditioned
`global_phase` node. Keep unconditional circuit global phase on `PhysicalDAG`.

Use Qiskit's DAG conversion for quantum-wire and classical-write dependencies.
Add a direct producing-measurement-to-operation edge for each condition.

Provide the reference tester `execute` and its records in `execution.py`.
Each run keeps fresh result and completion state. An operation starts when its
dependencies have completed and its condition's result is available. If the
condition is false, record a zero-duration skipped operation and release its
successors.

Separate measurement operation completion from measurement result delivery.
The latter occurs after the configured `measurement_latency`; the tester calls
the measurement backend at that result-delivery event. Other work that does
not read the result may proceed after its own dependencies complete.

Use abstract time units. Operations default to one unit; barriers and global
phase default to zero. Callers can override durations by operation name.
`ExecutionTrace` records executed/skipped operation times in ID order and
measurement results in availability order. Its duration includes outstanding
result delivery.

A measurement backend implements `measure(result: ResultRef) -> int`.
`FakeBackend` derives a repeatable bit from its seed and the measurement ID,
independently of query order, and accepts explicit per-result overrides.
These bits are test inputs for control-flow validation.

## Alternatives

- Reading mutable classical slots during execution would risk binding a
  condition to a later measurement that overwrote its intended result.
- A full control-flow interpreter is unnecessary for the accepted fixed-body,
  single-bit feedback scope.
- Combining measurement duration and result latency would prevent independent
  quantum-wire work from advancing when the operation itself has completed.

## Consequences

The frontend retains measurement-result identity, and the reference tester
produces a reproducible executed/skipped trace. Later logical-level work can
reuse the result and condition semantics.

The tester follows circuit dependencies and abstract time. The planned physical
compiler owns compilation progress and samples fake results while compiling
measurements, as defined in [ADR 0005](0005-case-driven-physical-compilation.md).
That compiler loop and Atom IR replay remain to be implemented.
