# 0014: Carry actual retained atoms between layers

## Context

Gate-site occupancy was used as a synonym for selected reuse. Device profiles
can require local rotations to move an unrelated atom into the gate zone.
That atom then became a false anchor. A later CZ could fail because its two
endpoints occupied different catalogue pairs.

The reference checks both reuse membership and the previous entanglement
placement. Its local-gate emission does not relocate atoms. Our Device-driven
local placement needs to preserve the membership condition explicitly.

## Decision

The compiler carries immutable `retained_atoms` snapshots between fragments.
They identify the atoms selected to stay, including their position and holder.
They are compilation metadata, separate from Atom IR and physical layout.

`reuse_qubits` remains the outgoing proposal for the following CZ layer.
`retained_atoms` is the incoming selection actually kept by the previous CZ.
Placement anchors only current participants whose snapshots still match their
actual state. After a CZ, the strategy records only proposed atoms whose
execution and final states are equal. Local and readout fragments preserve
existing snapshots only when their entry and final states still match.
Returning to an old location cannot recreate a previously cleared selection.

Before multi-pair CZ placement, nonretained atoms in pulse coverage move to
initially vacant reachable storage. A `PlacementPlan` can return this optional
`preparation_layout`. The strategy emits its transport before the normal
arrival, pulse, and departure. Both greedy and IDS placement use this path.
Actual retained participants stay in place.

## Alternatives

Inferring retention from position loses the distinction between reuse and
local-control placement. Ignoring a conflicting anchor alone leaves occupied
target sites and possible closed transport cycles. Searching for the minimum
set of atoms to evacuate adds a separate optimization problem.

## Consequences

Local-control relocation no longer creates incompatible implicit anchors.
Preparation adds transport only when nonretained atoms occupy pulse coverage;
its time and actions are included in Atom IR. This preparation requires enough
vacant reachable storage. The normal storage-return and matching-reuse paths
keep their existing behavior.

Regression tests cover both placers, conditional local relocation, stationary
true anchors, full gate-zone occupancy, retention lifecycle, action legality,
measurement timing, and final replay.
