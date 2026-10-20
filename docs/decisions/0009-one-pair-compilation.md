# 0009: One-Pair Physical Compilation

Status: Implemented on 2026-10-20.

## Context

The native frontend and module contracts are implemented. The next increment
connects those contracts into a complete two-atom compilation path.

## Decision

The compiler owns DAG progress, the current layout, planned time, and sampled
measurement values. A strategy selects a ready layer and returns an AtomFragment.
The first strategy composes the existing scheduler, placer, and transporter
interfaces with a small action emitter.

The module sequence is:

```text
resolved ready operations -> selected layer -> execution and exit layouts
    -> transport to execution layout -> pulse/readout -> transport to exit layout
```

Use fixed SLM gate pairs contained in a CZ-capable zone and within the device's
interaction radius. Select the assignment with the shortest total movement.
Keep the pair at its execution layout between gates.

Transport two atoms together when their source and destination share a row,
preserve column order, and satisfy AOD spacing and capacity. Otherwise use
singleton transfers to vacant destinations. This profile uses at most two atoms,
one transport AOD, and SLM boundaries. Cyclic swaps needing a temporary site
remain outside this transport implementation.

Measure one atom at a time in AOD, then return it to its preceding SLM site.
The return permits subsequent local operations and frees the AOD for the next
transport. Readout must retain the atom in this profile. Device capability
records supply channels, pulse/readout durations, holder permissions,
interaction radius, readout spacing, and feedback latency.

Sample each source measurement once while compiling its measurement action.
Record its availability as action completion plus feedback latency. Independent
ready work can fill that time; emit WAIT only when the next condition requires
a result that is not yet available. False conditions release successors without
emitting actions. Selected global phases accumulate on AtomProgram.

Replay applies the generated state transitions. Full transport checks remain
test/debug oracles and are not called by the constructor or compiler loop.

## Reference

The execution-layout, pulse, exit-layout sequence follows
[MQT QMAP v3.5.0 CodeGenerator](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/code_generator/CodeGenerator.cpp#L167-L201).
The one-pair placement and transport rules are a minimal implementation of this
sequence. AOD readout and fake measurement feedback are NADiT additions.
IDS placement, general relaxed routing, and multiple simultaneous pairs follow
after this closed path.

## Alternatives

A single implementation file would couple DAG progress to geometry and action
construction. A general routing search would add machinery before the input,
feedback, action, and exit-state contracts have been exercised together.

## Consequences

The output contains a complete case-specific AtomProgram, operation decisions,
measurement records, and an exit layout. This validates the physical compilation
path with a fake result provider; it does not simulate quantum state evolution.
