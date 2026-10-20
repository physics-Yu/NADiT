# 0016: Relaxed routing over staged transport

Status: Implemented on 2026-10-20.

## Context

Staged strict transport supports row/column transfers and offsets while
preserving endpoint order. Search Smarter's relaxed router can combine
endpoint-order changes by loading rows in their final order and releasing
columns in their source order. Every individual MOVE still preserves the
order of currently active lines.

Reference: MQT QMAP commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`,
`IndependentSetRouter` and `CodeGenerator::RearrangementGenerator`.

## Decision

Add `RelaxedTransport` behind `Transporter.construct`. Retain direct strict
and staged strict as comparison modes. Separate compatibility/cost, group
merging, device admission, staged construction, and Atom IR emission.

An axis must preserve equality: a shared row/column cannot split and distinct
rows/columns cannot merge. Order inversion is a soft conflict. Its cost is
the sum of source and target axis separations. When both axes invert, combine
costs as `(cbrt(x_cost) + cbrt(y_cost)) ** 3`.

Start with distance-descending strict groups. Visit donor groups backwards.
For each donor atom, choose a recipient by the reference incremental proxy:

```text
max(cbrt(atom_distance) - cbrt(recipient_max_distance), 0)
    + cbrt(maximum_pair_offset_cost)
```

Commit a redistribution only when every donor atom has a recipient and:

```text
donor_max_distance > prefer_split * sum(incremental_proxy_costs) ** 3
```

The default `prefer_split` is 1.0. Rejected redistributions retain their
strict groups. This reference proxy is separate from Device microseconds;
actual LOAD/MOVE/STORE durations continue to use the configured timing.

Device admission establishes a constructive staging shape, sufficient line
capacity and offset space, and a valid destination-occupancy order before
committing a merge. Unsupported shapes retain the strict construction.
No full action checker, replay, or duplicate two-plan emission is used to
decide routing.

Source row reordering reserves an envelope of the selected source rows plus
their count times the source vertical pitch. Storage column reordering uses
the analogous target-X envelope. Foreign-zone SLM sites cannot enter those
envelopes. Gate-column reversal admits at most two columns; with
`d=(pitch_x-pair_separation)/3`, the other column parks at `x-2d` or
`x-pair_separation-d`. Larger gate-column reversals retain strict groups.
Ordered gate-column release retains the existing complete-mask condition.
The reference generator uses one pair-separation value for a rearrangement.
Transfers between two gate regions with different pair separations therefore
use direct strict transport; this admission rule is shared with staged strict.

Generalize the existing staging generator's line assignment and axis choice
to the source algorithm. Bind complete active grids to the resulting events.
Compiler DAG progress, measurement sampling, feedback latency, and native
source identities remain shared.

## Verification

The completed modules pass 740 tests, compared with the preceding verified
676-test baseline. Ruff formatting, lint, and mypy also pass. Existing test
assertions and strict reference outputs are unchanged.

The pinned upstream C++ router matches all 12 small cases for mover order,
compatibility, proxy cost, strict grouping, and relaxed grouping. Its
rearrangement generator matches all 29 events across X, Y, and XY reversal
cases. The executable adapter, fixtures, and source fingerprints are described
in the [reference comparison](../../modules/physical_compiler/reference/relaxed_README.md).

Separate tests cover explicit-grid action legality, final replay, hard equality
conflicts, high-offset strict fallback, finite capacity/workspace, retained
atoms, both measurement outcomes, feedback latency, and different pair
separations between gate regions. Full checkers remain in tests.

The existing `multipair_feedback.qasm3` example gives the following comparison,
with the same Device, reuse enabled, and the same forced measurement outcome.
The relaxed threshold is 1.0. Atom counts include all LOAD/STORE bindings;
transport time sums generated LOAD/MOVE/STORE durations.

| Placement | Outcome | Staged strict loads/stores | Relaxed loads/stores | Staged strict transport (us) | Relaxed transport (us) |
| --- | --- | --- | --- | --- | --- |
| Greedy | 0 | 13/13 | 13/13 | 1490.425 | 1269.146 |
| Greedy | 1 | 21/21 | 21/21 | 2040.167 | 1818.888 |
| IDS | 0 | 13/13 | 13/13 | 1153.238 | 1049.779 |
| IDS | 1 | 21/21 | 21/21 | 1550.600 | 1447.142 |

All eight programs replay to their reported exit layouts. In these cases the
improvement comes from batching the same atom transfers: for IDS outcome 1,
LOAD actions decrease from nine to eight while atom-load count stays at 21.

## Consequences

Relaxed routing is an optional strategy using the existing Device and Atom IR.
It changes batch construction, not the per-MOVE prohibition on active-line
crossing. Rigid AOD routing and motion dynamics remain separate work.
