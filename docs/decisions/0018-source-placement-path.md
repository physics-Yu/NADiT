# 0018: Source placement geometry and candidate path

Status: Implemented on 2026-10-21; accepted before implementation on 2026-10-20.

## Scope

Implement the initial placement, nearest-SLM/site queries, candidate windows,
and scoring context from MQT QMAP revision
`cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`. Execute the original C++ methods on
the same small inputs and retain their outputs as reference evidence.

The preceding verified baseline was 897 tests. Modules were completed before
running the unified suite. Compiler-owned measurement sampling, conditional
dependencies, and feedback timing remain in the existing pipeline.

## Modules and handoffs

| Module | Responsibility |
| --- | --- |
| `placement/geometry.py` | Explicit regular SLM arrays, paired sites, and nearest queries |
| `placement/initial.py` | Storage filling direction and initial qubit-to-atom layout |
| `placement/windows.py` | Source window bounds, expansion, and candidate enumeration |
| `placement/candidates.py` | Source gate/storage jobs, orientation, order, and lookahead |
| `placement/coordinates.py` | Full free-site coordinates and nearest free storage columns |
| `placement/scoring.py` | Source distance metadata, full free-site ranks, and score context |
| `placement/heuristic.py` | Compose geometry, candidates, IDS, and existing layout handoffs |
| `reference/placement_*` | Pinned original C++ execution and same-input comparisons |

Geometry records ordered storage arrays and pairs of entanglement arrays.
Every array supplies site identities, row/column indices, pitches, and bounds.
Existing regular Device sites and the selected catalogue can supply this
metadata; callers can provide explicit array geometry. Geometry inference must
not silently turn a nonregular catalogue into a different candidate search.

Initial placement begins on the storage side nearest the first entanglement
array, fills columns in order, and continues through storage arrays. Explicit
entry layouts remain accepted. The multi-pair example uses automatic
initialization and shares its geometry with the IDS placer.

Empty storage arrays do not require a nearest-site query when every participating
atom stays in the gate zone. Automatic initialization still requires enough
storage sites for its requested atoms.

## Source candidate behavior

Nearest queries use the source SLM selection and grid projection rules, including
canonical source-site ordering and rounding. Occupancy does not redefine the
geometric nearest site.

Enable the source window by default: `use_window=True`,
`window_min_width=16`, `window_ratio=1.0`, and `window_share=0.8`.
Retain `use_window=False`. Candidate windows belong to the selected nearest
SLM and expand with the source gate/storage increments and enumeration order.
No extra candidate-count cap is introduced.

Preserve the source's reversed gate-option score-distance order separately
from actual movement endpoints. It is heuristic metadata; emitted movement
targets and Device timing use physical coordinates.

Scores use ranks from all relevant free sites, including those outside the
candidate window. Nearest storage columns supply the source storage scaling
context. Physical qubit identities determine source job tie ordering.

## Explicit source defects

The source reads beyond the last storage array when initialization exactly
fills capacity and resets the next array to the wrong end. Use the same
declared filling direction for every array and finish at the requested count.

The source window loop can stop making progress after covering its whole
nearest array. End expansion when no new cells remain; retain all candidates
in that array and let assignment determine feasibility.

When the nearest storage column is full, the source stores a boolean comparison
as the next minimum distance. Select the actual nearest free column instead.
The executed counterexample has source horizontal scale `1` and local scale
`4/3`; candidates agree, but scores and final placement can differ.

When two storage points straddle a gate zone in y, the source checks only the
first point and subtracts unsigned coordinates. Apply the same two-point
same-side condition that the source uses for x. The executed counterexample
selects the second gate zone upstream and the first declared gate zone locally.

These two corrections and the next-array initial-row correction have separate
fixtures retaining the raw original C++ output. Exact-capacity end-iterator
access and nonterminating exhausted windows are covered by local regression
tests rather than executed as ordinary oracle cases.

## Verification

The [placement reference](../../modules/physical_compiler/reference/placement_README.md)
executes the pinned original C++ geometry and placer methods. Twelve normal
fixtures agree on initial positions, nearest queries, ordered candidates,
source distance metadata, window expansion, compatibility-group scoring,
visited node costs and heuristics, and final placements. They cover 28 jobs,
223 candidate options, and 213 distinct score paths. Three further fixtures
record the explicit source corrections above.

Pipeline tests compile both measurement outcomes with automatic initialization,
windowed/full candidates, optional reuse, and strict/relaxed transport. They
check sampling once per measurement identity, feedback timing, individual
action legality, and final replay.

The unified suite passes 966 tests, compared with the preceding 897-test
baseline. Format, lint, and type checks pass. Two existing retention cases
initially exposed an unnecessary nonempty-storage requirement; the production
path was corrected without changing their assertions. Existing routing and
staging oracles are unchanged.

This evidence covers the placement component and its local compiler handoffs.
The C++ adapter does not execute the upstream scheduler, reuse analyzer, router,
or complete compiler.
