# 0017: Source implementation audit

Status: Implemented on 2026-10-20.

## Scope

Compare the existing pipeline with MQT QMAP revision
`cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`. Record source behavior, explicit
Device capabilities, and local corrections separately. A missing construction
case must not become an undocumented algorithm limit.

The preceding verified baseline was 740 tests. The affected modules were
completed before unified verification.

## Transport findings and changes

- Remove the two-column gate-reversal cap. The source router and generator
  operate on arbitrary numbers of columns.
- Remove the requirement to complete sparse gate batches into Cartesian
  blocks. Keep the source grouping order and the selected atom set.
- Match the source distance ordering, including descending atom IDs on ties.
- Correct the gate-column spreading boundary in the upward scan. The source
  tests the lower boundary while moving toward increasing X. At the left
  edge this changes the parking-lane phase and can park an unselected column
  on an occupied SLM site. Use the upper boundary in that scan. Preserve the
  remaining source pickup, spreading, combined-release, and offset loops.
- Use the source and target region's own pair separation in their respective
  offsets. Remove the cross-region equal-separation fallback.
- Bind capacity, spacing, and workspace to the actual coordinates constructed
  by the source template. Cache each batch's stages and emit that same plan.
  Remove whole-region workspace margins and count-times-pitch estimates.
  This binding does not run an Atom IR checker or evaluate two emitted plans.
- Use occupied active-grid intersections as routing dependencies. Empty fixed
  sites do not block a batch. Pending blockers can join a compatible strict
  batch; other occupied intersections prevent the proposed merge.
  Cache axis coordinates rather than their Cartesian product. Occupancy queries
  use the position index for small grids and actual atoms for large sparse grids.

For gate pitch `P`, pair separation `S`, and `d=(P-S)/3`, the parking lanes are
`L_k=kP-d` and `R_k=kP+S+d`. Increasing-X gaps alternate between `P-d` and `d`.
The original lower-boundary test steps from `-d` to `0`, corrupting this phase.
The corrected upper-boundary test steps from `-d` to `S+d`. The known
three-column case now parks at `[-2, 12, 14]`, replacing the unsafe
`[-2, 12, 18]` for `P=8, S=2`.

This is a documented source defect correction, not identical event reproduction
for the unsafe input. The original unsafe trace remains evidence.

## Scheduling and placement findings

Drain ready local rotations before selecting a CZ layer, as the source ASAP
scheduler does for static segments. This can expose independent CZ gates for
the same layer. Do not advance unresolved measurements or conditional nodes.

Match the source gate-candidate and storage-candidate tie ordering. Retain the
explicit gate catalogue, documented nearest-available-candidate adaptation,
configured IDS budget, and all-endpoint reuse correction.

## Device and compiler contracts

Keep configured AOD line capacity, minimum spacing, workspace, and occupied
destination dependencies. Keep the regular-array geometry required by the
reference staging template. These must be distinguished from algorithmic
column-count or occupancy-mask caps.

Measurement identities, one-sample FakeBackend behavior, feedback latency,
and compiler-owned DAG progress remain the existing application contracts.
Matching both reusable endpoints remains the documented correction in
ADR 0012.

## Audit inventory

| Step | Source behavior or retained application contract |
| --- | --- |
| QASM and native Physical DAG | Application frontend, stable measurement identities, and explicit feedback dependencies |
| Gate scheduling | Ready local rotations precede CZ selection; earliest measurement/reset boundaries remain compiler-controlled |
| Reuse | All shared-endpoint matching edges; documented correction to the source's `else-if` omission |
| Candidates | Existing finite Device catalogue and nearest-available-pair adaptation from ADR 0013; source distance and identity ordering |
| IDS and scoring | Source proxy, configured trials and queue capacity; deterministic ordering for otherwise equal heap priorities |
| Strict and relaxed routing | Source compatibility, distance ordering, donor scan, and merge cost; actual Device and occupancy binding |
| Staged transfers | Source row/column loops with the upward-boundary correction and endpoint-specific pair separation |
| Atom IR | Explicit complete active grids, Device distance-to-time timing, measurement feedback, and source operation coverage |

The fixed single-CZ-zone catalogue and flexible AOD backend remain the approved
current profile. They are not inferred restrictions of Search Smarter. Rigid
AOD compilation and other layout architectures remain separate implementations.

## Verification results

- `pytest -q`: 897 passed; 157 added to the preceding 740-test baseline.
- Ruff format and lint passed; mypy passed for 121 source files.
- All existing legal upstream router and event oracles remain unchanged and
  pass. The original unsafe C++ trace remains unchanged.
- Three, four, and six reversed columns complete as one batch. The three-column
  case uses a workspace left boundary of `-8` and reaches only `-6`.
- All 120 permutations of three selected columns from six left/right gate
  columns pass with stationary atoms in the unselected columns.
- Sparse strict batches, including the recorded six-mover collision input,
  complete without Cartesian completion or splitting.
- Exact capacity, spacing, workspace, cached-plan reuse, occupied intersections,
  and empty foreign SLM sites have targeted coverage.
- Local-gate chains expose two CZ gates in one pulse. Both measurement outcomes
  preserve sampling identity, feedback delay, per-action legality, and replay.

Two old regressions asserted the removed shortcut itself: mandatory splitting
of sparse groups and direct fallback across different gate pair separations.
Their expectations now require the supported construction while preserving
every action-legality and final-state assertion.

Full action checkers remain test tools.

## Sources

- [Router compatibility, merging, and distance ordering](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp).
- [Gate-column spreading and release](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/code_generator/CodeGenerator.cpp#L814-L1035).
- [ASAP scheduling](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/scheduler/ASAPScheduler.cpp).
- [Placement candidate ordering](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/layout_synthesizer/placer/HeuristicPlacer.cpp).
