# 0020: Placement decision parity

Status: Implemented on 2026-10-21.

## Context

The bounded source study has seven completed pairs whose first difference is
placement: graphstate 60 with A*, and graphstate 120, 180, and 200 with IDS in
both routing modes. Earlier scheduling and reuse outputs agree. The cause is
not established by the final layouts or aggregate costs.

## Decision

Use the saved native inputs, architecture, configuration, and original C++
pre-stage layouts. Start with graphstate 60's first gate placement and
graphstate 120's storage placement after the second CZ layer.

Observe the pinned C++ implementation and the local implementation at each
search decision: ordered jobs and candidates, coordinate ranks, compatibility
groups, cost and heuristic components, and queue insertion/removal order.
Identify the first differing decision before changing production code.

Keep the source algorithms in the reference runner. Instrumentation records
their decisions; it does not replace them with expected outputs. Fix the local
implementation at the responsible module boundary and add regressions from
the observed source behavior. Existing test assertions remain unchanged.

After the modules are complete, run the seven affected pairs, the ten
previously matching pairs, and the complete local checks. Preserve the previous
benchmark report as historical evidence and record the new comparison
separately. Measurement feedback, action legality, and terminal replay remain
part of the full suite.

## Alternatives

Final-layout comparison cannot identify the responsible search decision.
Attributing the difference to numerical precision or equal priorities before
tracing would leave candidate and score construction unchecked.

## Consequences

The reference tooling gains a reproducible decision trace. Production changes
are limited to the demonstrated placement differences. This task does not
repeat the full paper benchmark matrix or change its previous timing budget.

## Observed causes and corrections

Graphstate 60 A* has identical ordered candidates on both sides. At the first
non-root selection, C++ gives option 37 priority 171.20854377746582, strictly
below option 13 at 171.20855522155762. The previous local scorer gives both
171.2085551854771 and selects 13. Graphstate 120 IDS likewise changes its
first selection from source option 2 to local option 0.

Restore binary32 expression boundaries and the original accumulation order:
candidate means, selected lookahead, group cost, sorted-rank deviation, and
heuristic terms. Search priorities add the returned g/h values in double,
as the source search templates do.

After that correction, graphstate 200 still differs at the fourth IDS restart.
Ordered candidates and all 7,768 common g/h evaluations agree. The source
indexed queue removes path [112]; the previous serial-ordered queue removes
[29], both at priority 45.29306983947754. Restore the source paired min/max
heaps, strict comparisons, replacement, and shrinking operations.

The fixed isolated searches match the source physical selections and final
layouts. For 60 and 200, option paths and common scores also match exactly.
Equal-distance storage candidate permutations remain: 120 has two differing
job means and 3,395 common-node last-bit score differences; 180 has identical
common-node scores after normalizing candidate indices to physical choices.
The source comparator specifies distance order without specifying equal-key
order. These residual observations are retained in the evidence.

## Unified verification

Rerun the same saved inputs for the seven affected and ten previously matching
pairs on both engines: 34 successful runs with a recorded 120-second worker
budget. All 17 pairs match every placement; 15 match through the action trace.
Graphstate 120 and 180 relaxed first differ in transport. On each first affected
transition, strict groups and relaxed groups without staged admission match
C++; the difference appears when Device/occupancy admission is applied.
The original MOVE sequences activate grid intersections occupied by stationary
atom 92 at (171,309) in case 120 and atom 175 at (231,309) in case 180.
The original atom-addressed code generator emits those moves; the existing
Device model reserves every active AOD row/column intersection. Capacity,
spacing, and workspace pass for both batches. This is a recorded model
difference, separate from the repaired placement decisions.

The prior 30-second benchmark report is unchanged. The new report is
`modules/physical_compiler/reference/placement_recheck_results.json`;
`placement_decision_evidence.json` retains the causal observations and hashes.
Both raw diagnostic and recheck archives are local ignored artifacts.

Full checks pass: Ruff format/lint, mypy on 158 source files, and 1,096 tests
against the previous 1,078-test baseline. No existing assertion or oracle was
modified. Existing feedback, action-legality, and exit-state replay tests pass.
