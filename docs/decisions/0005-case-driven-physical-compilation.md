# 0005: Physical Compilation for One Measurement Case

Status: Accepted on 2026-10-20. Next implementation step.

## Context

The frontend provides a complete conditional Physical DAG. The fake backend
and reference executor have validated measurement-result binding and branch
selection. The next task is to map that DAG to atom actions under a selected
Device and compiler strategy.

## Decision

The physical compiler owns compilation progress, the ready set, predicted
atom placement and trap ownership, and the planned timeline. Its inputs are
the Physical DAG, Device capabilities, strategy configuration, entry state,
and a measurement-result provider.

Its output is the complete Atom IR for one measurement case, with source
operation links, timing, measurement values, and exit state. The input DAG
retains all conditional operations.

The compilation loop is:

1. Find operations whose predecessors have been compiled or skipped.
2. Resolve their conditions from recorded measurement results. Skip false
   conditions and release their successors.
3. Select a compatible group using Device capabilities and predicted state.
4. Generate transfers, movements, pulses, and measurements for that group.
5. For each compiled measurement, obtain its result once from the fake
   backend and store it under its source measurement identity.
6. Update predicted state and planned time, mark the source operations
   compiled, and continue until all DAG nodes have been processed.

Compiling a predecessor releases its successor for planning. Dependencies
also constrain the generated timeline: a conditional operation starts no
earlier than the producing measurement's planned completion plus feedback
latency. The compiler calculates this constraint without waiting for an
execution event.

The selected strategy can inspect the full DAG for lookahead. It owns gate
grouping, placement, routing, and action generation. One gate group may
require several transport batches and AtomSteps.

## Measurement cases

`FakeBackend` provides a deterministic bit for a seed and measurement
identity. All conditions referencing that measurement read the same value.
Source measurement identities remain stable through gate decomposition,
placement, and routing.

The existing IR and backend use `0/1`. For a provider exposing `+1/-1`, the
conversion convention is `+1 -> 0` and `-1 -> 1`.

A compiled measurement and a compiled ordinary operation both advance DAG
progress; the measurement additionally produces a value for later conditions.
A skipped operation advances progress without producing atom actions.

## Existing implementation

Keep the QASM frontend, `PhysicalDAG`, `ResultRef`, `Condition`, and
`FakeBackend`. Keep `execute` as the reference tester for DAG dependencies,
conditions, and abstract event timing.

[ADR 0004](0004-measurement-feedback.md) describes that tester's current
behavior. The compiler will call the measurement provider directly as it
processes measurements. Atom IR replay is a later implementation step.

## Alternatives

Driving compilation from every execution-completion event would restrict
planning to the current execution frontier. Advancing compilation from
scheduled predecessors permits optimization across consecutive gate layers.

## Consequences

The next implementation defines Device capabilities, the compiler context,
and Atom IR, then builds a complete DAG-to-atom-action strategy. Start from
the scheduling, reuse, placement, routing, and code-generation flow studied
in Search Smarter, Not Harder, and compare further strategies through the
same input and output contracts.

Component packaging and logical compilation follow the physical compiler.
They reuse result references and conditions; component binding must assign
distinct identities to measurements from different invocations.
