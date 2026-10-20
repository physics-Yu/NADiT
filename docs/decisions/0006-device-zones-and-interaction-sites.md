# 0006: Device Zones and Interaction Positions

Status: Accepted on 2026-10-20.

## Context

The physical compiler must compare a flexible zoned reference with a rigid
16-by-16 AOD device. Gate and readout placement depend on both spatial
capabilities and the traps carrying the atoms.

## Decision

Separate operation regions, fixed SLM sites, AOD controls, and interaction
positions. Zones can overlap. Configure CZ illumination independently from
interaction distance, and support SLM-SLM, AOD-SLM, and AOD-AOD holder pairs.

The first strategy uses fixed candidate interaction pairs with dynamic atom
assignment per layer. Device legality also permits later strategies to
generate candidate positions. AOD-readable measurement regions allow readout
without an intervening STORE.

Track holders and transfer costs during planning. Generate the concrete
transfer and movement instructions from that plan.

## Alternatives

Choosing arbitrary interaction coordinates in the first strategy adds a
position search to the reference pipeline. Equating zones with SLM arrays
would exclude AOD-held operations and overlapping operation regions.

## Consequences

Implement the flexible zoned pipeline first, then the rigid variant.
The [device design](../../modules/physical_compiler/docs/device-and-zones.md)
defines geometry, capability fields, and remaining configuration values.
The [implementation plan](../../modules/physical_compiler/docs/implementation-plan.md)
assigns files and acceptance criteria to each step.
