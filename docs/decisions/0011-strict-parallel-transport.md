# 0011: Shared Rules and Strict Parallel Transport

Status: Implemented on 2026-10-20.

## Context

The multi-pair baseline compiles complete measurement cases with sequential
singleton transport. Parallel transport must use the same PhysicalDAG and
AtomFragment interfaces, preserve feedback, and construct valid AOD actions.

## Decision

Add shared movement rules in `transport/rules.py` and a `StrictTransport`
implementation of the existing transporter interface. Keep the sequential
transporter as a reference strategy and for singleton AOD readout.

A MoveRequest contains an atom ID and its source and target positions.
StrictRules supplies pair compatibility, batch capacity/spacing, endpoint
frames, and transport duration. These helpers are also available to later
placement-cost evaluation.

Build one pairwise conflict map for the changed atom requests in each layout
transition. Strict compatibility preserves equality and spatial order on both
axes. Distinct active lines must satisfy the device's minimum spacing at both
ends. The graph does not encode row/column capacity or occupancy; those depend
on the selected group and current layout.

## Constructed batches

This increment uses simultaneous pickup, one endpoint movement, and
simultaneous release:

```text
LOAD complete source grid
MOVE same line identities to the target grid, when positions change
STORE all carried atoms and deactivate the grid
```

The AOD is empty at SLM-to-SLM batch boundaries. Line IDs follow source-axis
order and retain that identity at the destination. Transfer durations come
from Device; movement duration is the maximum atom distance times the
configured distance-to-time coefficient.

Maintain a current occupancy index. To admit a candidate, close the group
over every occupied source or target grid intersection:

1. If an intersection contains another selected atom, it is already covered.
2. If it contains a pending atom that fits the group's rules and capacity,
   include that request and process its additional intersections.
3. If it contains an atom that cannot join, reject this candidate extension.

Repeat until no further atoms need to be included. This handles both ghost
pickup and targets that can be vacated by loading their occupants together.
A stationary spectator can force requests into separate batches. After a
batch completes, update occupancy and retry remaining requests against that
new state. A fixed axis representative and the existing coordinate tolerance
keep frame construction and occupancy lookup consistent.

Each chosen group emits existing Load, Move, and Store actions. No full
`check_load`, `check_move`, `check_store`, or layout validation is performed in
batch selection or action generation. Tests apply these independent reference
checks to every generated phase.

## Scope

The direct template can split more batches than staged row pickup with offsets.
It supports multiple active rows and columns, including sparse grids whose
other intersections are empty or belong to included atoms. Closed dependencies
that require an external buffer, parked AOD groups, and offset-based transport
remain subsequent constructors.

Retained singleton AOD readout continues through SequentialTransport.
The compiler still owns result sampling, condition selection, and timing.
Gate placement, pulse generation, and measurement identities are unchanged.

## Verification

- Pairwise order/equality conflicts and complete-frame line identity.
- Separate row and column capacity, including a group whose pairs all fit.
- Source and target ghost intersections, closure chains, and changing occupancy.
- Every emitted phase passes reference movement and state checks.
- Existing multi-pair QASM compiles both measurement outcomes with identical
  operation choices, result identities, and exit state under both transports.
- Strict transport emits parallel moves and reduces transfer steps on the
  existing study; readout and feedback waits remain correctly timed.
