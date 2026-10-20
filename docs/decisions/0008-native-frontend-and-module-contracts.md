# 0008: Native Frontend and Module Contracts

Status: Implemented on 2026-10-20 for native translation and module contracts.
Compiler algorithms follow separately.

## Context

The physical compiler should receive native operations. Frontend translation
must preserve feedback dependencies when gate expansion changes node counts.
Algorithm modules need small data contracts and independent options.

## Decision

The frontend follows:

```text
QASM -> parsed circuit -> source gate/condition expansion
     -> translation to GateSet -> PhysicalDAG
```

Use the parser's circuit representation until native translation is complete.
The default GateSet is `rx`, `ry`, `rz`, and `cz`. Measurement, reset, barrier,
and phase metadata retain their semantics. A caller can select another GateSet.
The translator reads gate capabilities without depending on device geometry.

Assign source identities after source gate/condition expansion and before
native translation. Each native child retains its source ID. Measurement
references use that stable source identity; DAG node IDs identify positions in
the final native circuit. Conditions bind the latest source measurement of a
classical bit, and DAG edges use its final producer node. Conditional
decomposition phases remain conditioned operations.

Organize reusable algorithms in frontend, scheduling, placement, transport,
and emission packages. A strategy composes implementations; each module owns
its options. Device contains geometry and operation capabilities. The compiler
context alone owns committed progress, layout, time, and measurement values.

Define small immutable handoffs: `GateLayer`, `PlacementPlan`, and
`AtomFragment`. Fragments contain existing AtomSteps and an exit layout.
Transport constructs legal phases; emission fills Atom IR fields directly.
Full transport checks remain test/debug tools.

## Alternatives

Decomposing an existing PhysicalDAG would require rewriting dependencies and
result references. A second transport-action IR would duplicate Atom IR.
A global options object would couple independently replaceable algorithms.

## Consequences

`load_qasm` and `loads_qasm` accept a GateSet and default to native CZ/rotations.
Reference feedback studies select their original gate basis explicitly.
Native frontend tests cover phase equivalence, condition dependencies, and
measurement identity across target gate sets.
