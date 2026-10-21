# 0029: Flexible AOD batch readout

Status: Implemented and server comparison completed on 2026-10-21.

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

## Server comparison

The two runs used implementation commit `a878fed`, the same 98-qubit,
800-CZ, 240-measurement input (SHA-256
`216b9970fd2886341e2f51945fb3afcd16ab473ff70fa57d33aa1598c43d85c3`),
and the same 240 outcomes fixed by measurement identity. Geometry was 64x64
storage sites, 8x40 gate pairs with 6 um row spacing, and the readout region
at y=602..622 um. Both runs used IDS trials=16, queue_capacity=2000, strict
CZ transport, matching reuse, and a 1,800-second worker budget. Only
readout.max_atoms changed from 1 to null.

| Metric | Singleton | Batch |
| --- | ---: | ---: |
| Compile wall time (s) | 210.938 | 213.293 |
| Total time including result availability (ms) | 244.548 | 109.855 |
| Transport time (ms) | 240.645 | 109.257 |
| Readout-zone MOVE time (ms) | 144.080 | 16.932 |
| Readout-zone MOVE actions | 480 | 56 |
| Exposures / measurement identities | 240 / 240 | 28 / 240 |
| LOAD / STORE actions | 771 / 771 | 559 / 559 |
| Atom loads / stores | 2604 / 2604 | 2604 / 2604 |

Batch counts by round were 4, 5, 6, 7, and 6, with at most 28 atoms in an
exposure. All 28 batches returned to their exact entry layout. Both results
passed independent semantic validation and final replay; QASM, DAG, strategy,
geometry (apart from readout capacity), and measurement values were compared.
Total time fell by 55.08%; readout-zone MOVE time fell by 88.25%. These are
compiler timing-model results with FakeBackend outcomes, not logical-error
or measured hardware performance.

Local artifacts are in
`modules/physical_compiler/artifacts/surface-d5x2-r5-batch-readout/`:
`experiment.json`, `comparison.json`, `run/`, and `index.html`. The HTML
contains both traces, with batch readout selected first. Its model checks
preserve 10,026 action boundaries and AOD alignment at 5,013 intermediate
samples, with no display-lane fallback. Browser visual acceptance remains
open because the browser tool cannot inspect local file URLs.
