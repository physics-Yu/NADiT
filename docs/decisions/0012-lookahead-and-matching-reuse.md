# 0012: Following-Layer Preview and Matching Reuse

Status: Implemented on 2026-10-20.

## Context

The multi-pair pipeline returns all CZ participants to storage. Consecutive
CZ layers can share atoms, including atoms that change interaction partners.
Keeping every shared atom can pin two new partners to different gate pairs.

## Decision

Enable reuse by configuring `SearchSmarterStrategy` with a
`MatchingReuseAnalyzer`. The default remains the existing storage-return
baseline. The strategy exposes `lookahead_enabled` and accepts an optional
`following` GateLayer in `lower`. Placement keeps its `reuse_qubits` argument.

The compiler previews the next CZ layer before lowering the current CZ layer.
The preview copies the ready frontier and uses a sparse dependency-count
overlay. It uses the same deterministic, stateless scheduler as compilation.
It can pass resolved barriers, phase metadata, false conditions, and addressed
rotations. It uses results from measurements already compiled, including
results whose feedback latency has not elapsed in the planned timeline.
It stops at a selected uncompiled measurement, reset, unsupported operation,
or a frontier condition whose result has not been compiled.
It never calls the measurement backend or commits progress or phase.

The compiler remains the only owner of DAG progress and measurement records.
FakeBackend is called once when a measurement is compiled; all later reads
use that record. Actual conditional operations still require measurement
completion plus feedback latency. Previewing a known branch does not release
its operations early.

An independent CZ layer may execute before a previewed conditional layer.
Placement uses the actually selected layer and clears nonparticipating retained
atoms from pulse coverage. The preview supplies a reuse opportunity rather
than fixing the later schedule.

The reuse analyzer constructs a bipartite graph of current and following CZ
gates. A shared qubit labels an edge. Maximum matching selects one retained
qubit per matched pair of gates. An identical unordered gate pair retains
both atoms. Both endpoints contribute candidate edges.

Placement reserves gate pairs containing retained participants before assigning
unanchored gates. A new partner occupies the other site of its anchor's pair.
The current layer's selected reuse atoms remain at their execution sites in the
exit layout; other participants and pulse-region spectators receive storage
destinations. Following [ADR 0014](0014-explicit-retained-atoms.md), the compiler
carries actual retained-atom snapshots. The following placement requires both
explicit retention and an unchanged current state before fixing an anchor.
Strict transport uses its existing occupancy and movement rules.

No following layer means no new retention. Final CZ participants return to
storage using the placement policy; their individual storage sites need not
equal the circuit entry sites. Atom IR and result identities are unchanged.

## Alternatives

- Retaining the intersection of the two layers' qubit sets can block partner
  changes. Matching selects compatible anchors.
- Sampling future measurements would select a branch before its measurement
  is compiled. The preview stops instead.
- Rebuilding or executing a second DAG would duplicate progress and feedback
  logic. The preview reads current progress and changes only local deltas.

## Consequences

Matching maximizes retained atom count. Stationary anchors can split strict
movement batches, so fewer atom transfers can take more time. The study
reports atom-load/store counts, batch counts, and transport duration separately.
Transport-aware placement remains the next optimization step.

## Verification

- Maximum matching, identical pairs, and both-endpoint candidate construction.
- Preview immutability, capacity splits, known conditions, and measurement stops.
- Compiled results usable during feedback latency, with conditional actions
  still delayed until their recorded availability time.
- Partner changes with stationary anchors and correct full-zone CZ interactions.
- Both feedback cases, source coverage, result latency, legal strict transfers,
  and final Atom IR replay.
- Comparison with reuse disabled on the existing multi-pair circuit.
