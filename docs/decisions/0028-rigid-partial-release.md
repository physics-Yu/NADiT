# 0028: Rigid transport with partial release

Status: Paused on 2026-10-21 at the user's request; optimization incomplete.

Only this design and an unexecuted test draft were written. No rigid-drop
planner, transport, placement score, or registry entry has been implemented.
The test draft is preserved in `drafts/0028-rigid-drop-tests.py.txt`, outside
test discovery. Existing rigid strict transport remains the active baseline.
The sections below describe the proposed continuation, not delivered behavior.

## Scope

Keep the existing SLM sites, gate pairs, zones, timing, and circuit cases.
Add `rigid_drop` transport alongside the existing strict baseline. A trip
loads one grid, translates it, and releases subsets at their destination
sites. Remaining atoms continue on the same rigid grid. No new lines are
activated while carrying atoms.

## Planning and emission

The planner returns trips with a source grid, bindings, and ordered release
stops. A stop records the translated grid and the subset of lines retained
after release. Released atoms must lose at least one supporting line;
retained atoms keep both lines. Source intersections include all occupied
atoms they would pick up. At each stop, active intersections avoid stationary
atoms, including atoms released at earlier stops. Every active line remains
inside the AOD workspace. These are incremental construction rules, not
calls to the full motion validator.

Start from legal direct strict batches. Try adding pending requests, closing
source pickup dependencies, and order legal release groups by travel time.
Accept merges only when their planned time beats executing the constituent
trips separately. Keep the direct plan as a time fallback for fixed endpoints.
This is a deterministic greedy policy, not an optimal route solver.

Emission translates the selected plan into LOAD, MOVE, and STORE actions.
The planner charges exactly the Device transfer durations and distance
function used by emission. AOD-holder readout boundaries retain the existing
sequential path. Flexible devices reject `rigid_drop` with exit code 4.

## Placement score

IDS and A* retain their existing candidates and search engines. Add an opt-in
`transport_score: rigid_drop` placement option; `source` preserves the
reference score. The new score groups partial assignments using the same
trip builder and Device timing. Complete assignments use the full transport
planner with current stationary occupancy. Search remains bounded by the
existing budgets. The score belongs to compilation, independently of the
post-processing noise models in ADR 0026.

The first implementation keeps the existing gate-then-storage searches.
It changes the transport model in both searches, without adding a nested
two-layer evaluator, changing candidate orientations, or changing geometry.
Next-partner guidance must be expressed in time when used with this score;
unknown measurement branches are never sampled by placement.

## Validation and comparison

Cover shared long travel with different release offsets, row/column
deactivation dependencies, capacity, workspace, source ghosts, occupied
intermediate stops, retained atoms, and empty trips. Replay every action
with independent motion checks. Check full circuit semantics and both
measurement outcomes using the existing FakeBackend.

Compare three modes on identical inputs: strict with source score,
rigid_drop with source score, and rigid_drop with its time score. Report
LOAD/STORE actions, transferred atoms, MOVE actions, and transport time;
do not assume all circuits improve. Keep the old test assertions unchanged.
