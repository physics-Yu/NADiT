# 0019: Complete source pipeline and evaluation

Status: Core implementation verified on 2026-10-21; bounded benchmark in progress.

## Scope and order

Use MQT QMAP revision `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`.
The preceding verified baseline is 966 tests. Complete the modules before
unified testing, then execute source comparisons and benchmark ablations.

1. Execute the original static pipeline on fixed native circuits, architecture,
   and configuration. Export gate layers, reuse sets, placements, routing
   batches, and generated actions. Report the first differing stage.
2. Add source capacity calculation and static scheduling semantics. Execute
   source scheduler/reuse comparisons. Keep original endpoint-priority reuse
   separate from the existing complete-shared-edge correction.
3. Compare the complete single-zone generation path, then connect multiple
   entanglement zones, clearing, and pulse coverage. Represent source global
   RY and local single-qubit semantics explicitly. NAViz export is deferred.
4. Add the paper's distance-to-time profile and A* using the existing candidate
   and scoring modules.
5. Run fixed-input benchmarks and ablations with the source configuration.
   Record transfer counts, movement duration, compilation time, and failures.
   Distinguish small end-to-end evidence from full published-scale evaluation.

## Contracts

The compiler continues to own DAG progress, planned state, measurement records,
and feedback timing. Each measurement identity is sampled once. Unknown
measurements stop related lookahead; static source comparisons do not sample.

Reference traces observe original C++ decisions. Adapters provide input/output
and dependency interfaces, not replacement scheduling, matching, placement,
routing, or rearrangement algorithms. Source files are pinned and fingerprinted.
Outputs retain original source defects; corrected local behavior is reported
as a difference rather than substituted into the oracle.

Optional trace collection records existing stage decisions without rerunning
placement or transport. Without tracing, compilation keeps the existing
lightweight handoffs. Algorithm modules remain independently selectable.

Original reuse and the complete-shared-edge policy are named separately.
Existing applications retain their current default. Static reference runs
select the original policy explicitly.

Multiple entanglement zones share one layer pulse covering the declared
regions. Region membership remains separate from atom holder type. No new
multi-AOD allocation scheduler or dynamics simulation is required.

## Module boundaries

| Path | Responsibility |
| --- | --- |
| `reference/pipeline_*` | Original C++ pipeline execution and stage comparison |
| `scheduling/` | Static source layers and architecture-derived capacity |
| `reuse/` | Original and complete-shared-edge matching policies |
| `placement/` | Existing candidates/scores with selectable IDS and A* |
| `placement/catalog.py`, `placement/geometry.py` | Multiple gate regions and regular arrays |
| `emission/`, `atom_ir.py` | Addressed/global controls and multi-region CZ |
| `device.py` | Linear and paper distance-to-time profiles |
| `examples/`, `reference/` | Reproducible studies and source benchmark evidence |

## Implemented source profiles

`SourceASAPScheduler` precomputes the original static phase sequence and capacity
while the existing compiler owns completion. `SourceReuseAnalyzer` keeps the
original endpoint-priority graph and BFS/DFS matching. The existing online
scheduler and complete-edge reuse policy remain available for feedback studies.

`GatePairCatalog.from_zones` connects multiple regular gate arrays.
`RegionSet` preserves the union of illumination regions without filling their
gaps. Initial placement and nearest queries retain separate array identities.
GlobalRY and LocalU have explicit Atom IR actions and control capabilities.

The source GlobalRY range is the storage bounding rectangle, including the
final lattice pitch. A fixture retains an atom outside that range at a global
pulse. The reference profile preserves that source output. Device global
control accepts an explicit region; applications can configure a region that
covers all intended atoms.

Intermediate source offsets use signed integer arithmetic. Explicit
`StagingGeometry.coordinate_quantum=1.0` reproduces integer micrometre divisions;
the default continuous profile remains unchanged. Input Rydberg illumination
ranges are consumed directly by both adapters, including extents wider than the
trap coordinates.

The source generator's comment assumes a common source and target zone, but the
original router can produce cross-zone batches. The generator takes lattice
bounds from the first mover. The local constructor keeps that convention and
binds the constructed batch to actual resources and occupancy; region identity
alone no longer splits the batch.

## Executed full-stage reference

The C++ adapter executes the original scheduler, reuse analyzer, full multi-layer
placer, router, and full code generator in the original order. It substitutes
only native-operation and output-container interfaces. It does not compile the
outer Compiler template, full QC/NA libraries, original NA validator, or NAViz.

Eleven fixtures were executed. Nine return complete results matching local gate
layers, reuse sets, all placements, ordered batches, and normalized atom actions.
Two original placement outcomes remain separate: a cross-storage input raises
`unordered_map::at`, and an exhausted multi-zone window does not terminate
within its recorded 30-second limit. The completed source stages are preserved.
The corresponding local cases complete.

One additional comparison selects complete-edge reuse and reports its first
difference at reuse, before comparing any downstream choices. Independent tests
cover multi-zone interactions, action legality, final replay, and both feedback
outcomes with once-only measurement sampling.

## Evaluation

The source benchmark preprocessing uses Qiskit CZ/ID/U1/U2/U3, optimization
level 3, and seed 0; measurements and barriers are removed for that static
study. Preserve generated native inputs so all variants receive the same
circuit. This preprocessing does not replace the feedback frontend.

The source movement model takes the largest Euclidean atom displacement in a
MOVE. For distance d in micrometres, time in microseconds is
`200 * (d / 110) ** (1 / 3)` up to 110, then `200 + (d - 110) / 1.1`.
Transfers use the configured duration. Report rearrangement duration separately
from total program time and compilation wall time.

Compare A* strict, IDS strict, and IDS relaxed. Keep reference behavior and
documented local corrections distinct. A timeout, failure, or unrun size is
reported as such; component tests do not establish complete reproduction.

The first full-matrix study uses a recorded 30-second budget per run; the runner
retains the original 900-second default. Its results cannot establish the
paper's timeout or release-build speedup claims. Original C++ bodies run in an
O3 standalone executable; local timings include Python Device/DAG construction.

Equal-priority heap order and numeric score accumulation retain their documented
local differences. A first A* placement difference on `graphstate_60` is recorded;
the cause has not been isolated to either factor. Matching the A* control flow
does not establish identical search decisions on every input.

## Unified verification

The completed modules pass 1,077 tests against the preceding 966-test baseline.
Ruff format, lint, and mypy pass. Existing test assertions are unchanged; two
test-helper coverage annotations were extended to accept region unions.

The initial full-suite failure exposed the old same-zone routing restriction;
removing that restriction and checking actual constructed plans resolved it.
Additional regressions retain explicit pulse extents, keep SLM grouping
independent of overlapping illumination, and prevent stale benchmark traces
from being compared after a failed or timed-out rerun.
