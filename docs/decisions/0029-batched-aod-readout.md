# 0029: Flexible AOD batch readout

Status: Implemented on 2026-10-21; server comparison pending.

## Contract

Add `ReadoutCapability.max_atoms`: 1 by default, a positive integer for a
finite exposure capacity, or null for no additional count limit. Device
geometry, AOD capacity, and readout spacing still apply. Existing jobs retain
the singleton path. Ready scheduling uses this capacity to collect independent
measurement nodes whose conditions have already been resolved by the compiler.

Batch readout is supported for retained-atom readout in a flexible AOD. Job
admission rejects unsupported motion/holder combinations when batch readout
is requested for a circuit with measurements. This does not enable rigid
partial-release transport (ADR 0028 remains paused).

## Modules

`readout/planning.py` constructs source-grid-closed batches from selected
measurement atoms. Pickup closure may include other selected measurements,
but never unrelated data atoms or measurements outside the ready layer.
Sorted source axes are placed monotonically in a measurement region with
axis spacing at least the readout spacing and AOD minimum spacing. Nearest
axis projections and regular-grid translations avoid occupied intersections;
smaller groups remain candidates if a larger placement does not fit.
The existing transport rules supply endpoint compatibility, line capacities,
grids, and timing. Full validators are not called during construction.

`readout/emission.py` translates a batch into LOAD, MOVE, one multi-result
Measure, reverse MOVE, and STORE. Zero-distance moves are omitted. Line IDs
stay stable. The exit atom layout equals the entry layout. Each measurement
keeps its physical ResultRef and source-operation ID. Batch duration is one
Device readout duration, not duration multiplied by atom count.

`SearchSmarterStrategy` delegates measurement layers to this component when
configured; its CZ/local paths and placement algorithms remain unchanged.
The compiler remains the only owner of DAG progress and FakeBackend sampling.
Each result becomes available at its batch measurement end plus feedback
latency. The batching planner never samples a result or touches the ready set.

## Verification and comparison

Test singleton compatibility, source ghost closure, capacity/spacing limits,
multirow readout, stationary occupants, zero-distance moves, distinct result
identities, feedback waits, and exact return layouts. Validate every emitted
action independently in tests. Add readout-capacity semantic validation so
saved programs cannot bypass the Device exposure limit.

On the server, run the existing two-patch d=5, five-round QASM with the compact
6 um gate-row architecture. Compare max_atoms=1 and null on the same frozen
code, geometry, timing, and search budget. Fix outcomes by ResultRef using the
previous completed run's measurement records. Report actual batch counts and
timing without assuming one batch per round. Preserve both results and export
their animations from saved results.

## Local verification

The full suite passes 1,626 tests against the 1,606-test baseline. Formatting,
lint, and mypy pass. Existing assertions are unchanged; the existing semantic
action fixture now explicitly declares unlimited readout capacity because its
tests already exercise simultaneous measurements. New cases cover shared
measurement identities, one sample per identity, all four two-bit feedback
outcomes, repeated measurements, zero movement, pickup closure, occupied
readout corners, finite line/region capacity, and independent replay.
