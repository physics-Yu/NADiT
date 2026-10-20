# 0015: Staged strict transport

Status: Implemented on 2026-10-20.

## Context

The direct strict transporter uses one common LOAD, endpoint MOVE, and STORE.
QMAP's strict router also uses staged pickup and release during code generation.
Partial grid occupancy therefore need not force separate rearrangements.

Reference: MQT QMAP commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`,
`IndependentSetRouter.cpp` and `CodeGenerator::RearrangementGenerator`.

## Decision

Add `StagedStrictTransport` behind the existing `Transporter.construct` interface.
Keep `StrictTransport` as the direct-transport comparison. Both produce the
existing Atom IR and use Device geometry, AOD limits, and distance-based timing.

Separate three responsibilities:

1. Select strict compatible groups, ordered by descending movement distance.
   Preserve row/column equality and order and admit only groups that fit the
   device's line capacity and available staging space.
2. Construct row/column pickup, offsets, travel, and release from fixed-site
   geometry. Use identical occupied-column/row sets to combine transfers.
3. Emit complete active AOD grids and affected atom targets in LOAD/MOVE/STORE.
   Retain stable line identities and include every collective displacement.

For strict groups, use the reference axis selection: fewer columns than rows
selects column pickup/release; fewer rows selects row pickup/release; equal
counts selects row pickup and column release. Offsets use SLM spacing and
gate-pair geometry. Active lines remain ordered throughout each MOVE.

Construct actions from admitted geometry and occupancy. Full `check_load`,
`check_move`, `check_store`, and replay are verification tools, not candidate
filters. Unsupported staging geometry can use the existing direct constructor;
the compiler's feedback loop and singleton AOD readout path remain shared.
Region admission requires one regular array or a consistent paired gate array,
including common pitches, subarray bounds, and sites on the declared lattice.
Different array pitches within one region use direct transport.

## Verification

Complete the modules before running the verification suite, as requested.
Compare small cases with executable pinned upstream code, recording the source
revision, extraction/build procedure, coordinate units, and any intentional
Device adaptations. Do not substitute a handwritten Python reference for an
upstream execution result.

Cover row/row, column/column, row/column, sparse pickup, combined transfers,
both transport directions, gate-pair offsets, capacities, stationary atoms,
measurement feedback, per-action legality, and final replay. Compare against
the direct strict baseline using the same circuit, device, and result.

## Consequences

One rearrangement can contain multiple transfers and moves. Each action has
its own timing; a batch is not a single TICK. No relaxed compatibility,
order-changing routing, dynamics simulation, or new circuit semantics is added.

## Sparse gate-column admission

Executing the original C++ exposes a sparse gate-column case where its initial
column spreading parks a moving atom over a retained SLM atom. The source
assumptions of regular arrays and strict axis order hold in that case.
[The recorded input and trace](../../modules/physical_compiler/reference/unsafe_sparse_gate.json)
preserve the evidence.

The initial implementation restricted these batches to two columns or complete
Cartesian blocks. [ADR 0017](0017-source-routing-audit.md) removes that shortcut
and corrects the upward parking boundary responsible for the collision.
Sparse multi-column batches now use the same source loops. Device binding
uses the actual cached stage coordinates and occupied active intersections.

## Results

- 676 tests passed, compared with the preceding 637-test baseline.
- Nine supplied batches, with 77 events in total, match executed upstream C++
  in event order, atom IDs, and coordinates.
- The explicit-grid actions additionally pass load/move/store checks and replay.
- Both measurement outcomes pass with greedy and IDS placement and matching reuse.

With the shared small reference fixtures and `load_us=store_us=10`,
`move_us_per_um=1`:

| Case | Direct time (us) | Staged time (us) | Maximum moved atoms, direct / staged |
| --- | ---: | ---: | ---: |
| `row_row_sparse_up` | 285.073 | 180.021 | 2 / 4 |
| `col_col_sparse_up` | 295.035 | 187.891 | 2 / 4 |
| `row_col_sparse_up` | 285.036 | 175.394 | 1 / 2 |

Each sparse case still has two LOADs and two STOREs. The saved time comes
from the shared long move after staged loading. On the existing single-row
multi-pair circuit with IDS and reuse, atom load/store counts stay at 13/13
for bit 0 and 21/21 for bit 1. Transport time changes from 1141.125 to
1153.238 us and from 1531.643 to 1550.600 us, respectively: these layouts
already fit direct batches, so staging adds offset time.
