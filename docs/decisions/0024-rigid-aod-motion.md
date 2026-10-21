# 0024: Rigid AOD motion model

Status: Implemented on 2026-10-21.

## Context

The device design defines two profiles: `zoned_flexible` (order-preserving
rows and columns, no count limit) and `zoned_rigid_16` (rigid translation, up
to 16 rows and 16 columns). It also defines an optional flexible 16-by-16
profile to separate capacity effects from motion-model effects. Before this
change, only flexible motion existed. `AOD` had no motion field, and
`motion.check_move` accepted any order-preserving line coordinates.

Transport compatibility was shared through `StrictRules`, constructed directly
in four places: `placement/heuristic.py`, `transport/strict.py`,
`transport/staged.py`, and `transport/relaxed.py`. Placement scoring uses the
same `compatible` relation to group moves. A rigid model therefore has to
change the rule object these sites receive, not only the transport class.
Otherwise placement would optimise for batches that rigid transport cannot
execute.

## Decision

### Device

Add `AOD.motion: Literal["flexible", "rigid"] = "flexible"`. Device JSON
accepts `"motion"` on each AOD entry, and existing configurations remain
flexible.

Rigid semantics:

- **LOAD** activates lines at legal coordinates, subject to the existing
  workspace, spacing, capacity, and ghost rules. Existing lines do not move
  during a transfer. As clarified for the experimental rigid model, new lines
  cannot be added while this AOD carries atoms (`rigid_loading`). Initial
  loading can activate the complete selected grid; loading at existing active
  intersections does not add lines.
- **MOVE** translates every active line by one common vector `(dx, dy)`: each
  row moves by `dy` and each column by `dx`, within `COORDINATE_TOLERANCE`.
  Spacing and order are therefore preserved automatically.
- **STORE** deactivates lines; this is unchanged.

`check_move` adds rule `rigid_translation` for rigid AODs. Line identity,
target, occupancy, and ghost checks are unchanged, so ADR 0023 validation
covers rigid programs without further changes.

### Transport rules

Introduce a `TransportRules` protocol in `transport/rules.py` with the methods
`StrictRules` already has: `compatible`, `can_fit`, `frames`, and
`duration_us`. `StrictRules` remains the flexible implementation. Add
`RigidRules`: two requests are compatible when their displacement vectors are
equal within tolerance and the existing source-axis sharing rules hold. That
is, atoms that share a source row or column also share it at the target.
`can_fit` and `duration_us` are the same as for flexible motion; the movement
distance is the common displacement.

Add `rules_for(aod, timing) -> TransportRules`, which dispatches on
`aod.motion`, and use it at the four construction sites. Placement scoring
then groups moves with the rigid relation automatically.

### Supported transports

| Transport | Rigid support |
| --- | --- |
| `sequential` | Supported: a single atom is a rigid translation |
| `pair` | Supported: pairs move together only with equal displacement, otherwise as singletons |
| `strict` | Supported through `RigidRules` |
| `staged`, `relaxed` | Not supported in this ADR: both change line spacing between pickup and release |

Unsupported combinations are rejected when the job is loaded, using the
support declaration from ADR 0025 once available. This implementation rejects
them in the registry with `ValueError` at `strategy.transport`, before building
the strategy. Direct Python calls reject them at the start of `construct`,
including no-op and singleton fallback paths: the constructors themselves
only carry an AOD ID and cannot inspect a Device. CLI job rejection retains
exit code 1; the support-declaration framework and exit code 4 belong to 0025.

### Profiles

Add three configurations with identical zones, sites, timing, and
capabilities, differing only in the AOD:

- `configs/zoned_flexible.json`: flexible, unlimited;
- `configs/zoned_flexible_16.json`: flexible, 16 by 16;
- `configs/zoned_rigid_16.json`: rigid, 16 by 16.

A study example compiles the same circuits on all three and reports the
ADR 0021 metrics side by side.

The comparison geometry has a 4-by-32 storage array at 5 um pitch and 20 gate
pairs on one row at y=140 um, with 2 um pair separation and 8 um pair pitch.
The measurement region starts at y=260 um. The three files use identical
zones, sites, operation capabilities, and timing. These are explicit study
settings; no architecture generator is introduced in this ADR.

### Follow-up

Rigid hardware usually loads one block, then alternates translation with
partial release ("shift and drop"). This can serve atoms with different
displacements from one LOAD. That transporter, and any rigid variant of
staged routing, need their own ADR after this baseline exists to compare
against.

## Open questions for the hardware model

- Must rigid line coordinates lie on a fixed frequency lattice, for example
  integer multiples of a tone pitch? If so, add an optional
  `AOD.line_pitch_um` checked at LOAD.
- Is there a maximum translation per MOVE beyond the workspace bounds?

The clarified model does not add lines while carrying atoms and uses the
same distance-to-time function as flexible motion. No frequency lattice,
maximum-step bound, or dynamics model is assumed.

## Alternatives

A rigid transporter that keeps `StrictRules` for placement would make
placement scores describe moves the transporter cannot perform. Modelling
rigid motion as flexible motion with a small capacity confuses the two
effects the 16-by-16 flexible profile is meant to separate.

## Consequences

Flexible behaviour is unchanged, and existing tests and reference fixtures
must pass without modification. Rigid strict batches are expected to be
smaller and more numerous than flexible ones; that difference is the
measured effect, not a defect. Staged and relaxed routing remain
flexible-only until the follow-up.

## Implementation order

1. `AOD.motion`, JSON loading, and the `rigid_translation` check. Accept:
   unit cases for equal and unequal row and column shifts, and unchanged
   flexible results.
2. `TransportRules`, `RigidRules`, and `rules_for` at the four sites. Accept:
   pairwise compatibility table tests; the flexible conflict maps and all
   reference comparisons are unchanged.
3. Rigid sequential, pair, and strict transport, with rejection for staged
   and relaxed. Accept: every emitted rigid MOVE passes `check_move`;
   feedback branches and replay pass; ADR 0023 validation passes.
4. The three profiles and the study example. Accept: one circuit set
   compiles on all three; the report shows batch counts, transfers, and
   duration.
5. Update device-and-zones.md, the README, architecture.md, and the CHANGELOG.

## Verification

The unchanged baseline passed 1,424 tests. After completing the modules, the
full suite passed 1,516 tests, with no changes to existing assertions. New
coverage includes common translations, unequal displacement splitting, line
capacity, occupied intersections, IDS/A* scoring, both feedback outcomes,
unsupported transport rejection, semantic validation, and final replay.
Ruff formatting, lint, and mypy also pass.

`examples.compare_motion` compiles identical capacity and feedback cases on
the three profiles. It reports actual action counts and transport duration
and can save validated `nadit.result/2` files for independent inspection.

For the 40-qubit, 20-CZ capacity example with IDS, strict transport, and seed
17, the measured results are:

| AOD profile | LOAD / STORE actions | Atom loads / stores | Transport time (us) |
| --- | ---: | ---: | ---: |
| Flexible unlimited | 7 / 7 | 80 / 80 | 1071.533330 |
| Flexible 16 by 16 | 8 / 8 | 80 / 80 | 1216.030772 |
| Rigid 16 by 16 | 44 / 44 | 80 / 80 | 6554.467672 |

All nine study cases (capacity and both feedback outcomes on each device)
pass semantic validation and final replay. These timings use the profiles'
linear distance function. The action counts describe this circuit, layout
search, and direct transport policy; they are not hardware-optimal bounds.
