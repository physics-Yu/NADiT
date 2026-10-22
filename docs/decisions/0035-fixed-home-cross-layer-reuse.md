# ADR 0035: Optional cross-layer reuse with fixed homes

Status: Implemented and tested, including controlled server comparison.

## Decision

Keep existing adaptive and per-layer QEC behavior. Add
`strategy.cross_layer_reuse: true` under `mode: qec`, with the existing
`reuse.kind: matching | source` selector. Omitted/false retains old behavior.

An optional `bind_entry` strategy hook receives the validated entry layout once
per compilation and returns a fresh bound strategy. The DAG controller still
owns readiness, results, timing and retained snapshots. No mutable homes survive
across compilations. Generic job/experiment code does not branch on this feature.

The new strategy keeps immutable SLM homes outside the CZ coverage. For CZ,
clear nonparticipant or invalid retained atoms to their own homes; use existing
gate candidate generation/search and matching; retain selected endpoints for a
known following CZ; send all others home. Skip adaptive storage search. Existing
IDS/A* and catalogue multipair placers supply the required execution placement.
The old ReferencePlacer and SearchSmarterStrategy remain available unchanged.

The window is the known CZ chain returned by existing lookahead. Rotations and
barriers may be crossed without changing gate order. Measurement, reset,
unknown feedback and the end of the circuit terminate lookahead. Return home
when no next CZ is known, and flush before measurement/reset. This first version
does not add QEC-round inference or new user-defined barrier semantics.

Local gates retain valid, unchanged gate-site anchors; relocated anchors are
released home. Retained atoms stay in SLM traps, not in AOD indefinitely. Homes
and qubit mappings remain fixed; exact standard geometry is restored at window
boundaries, not after every gate. Gate arrangements during a window are chosen
by existing algorithms; a symmetry-aware group selector remains future work.

## Validation and experiment

Test unchanged off-mode, repeated pairs, changing partners, spectator evacuation,
intervening rotations, both measurement branches, one sample per measurement,
feedback latency, full legality and final replay. Reusing the same unbound
strategy across different entries must not retain stale homes. Test config,
discovery and JSON Schema; unsupported placements report exit 4.

Run current off/on policies on the server with the previous two d=5 blocks,
five rounds, fixed Device/outcomes/noise and the same IDS budget. Compare atom
transfer counts, transport batches/time and loss. Do not assume fewer atom
transfers imply shorter parallel duration. Preserve prior experiments and add
the new cases to the shared viewer.

Verification: 1,770 tests passed (baseline 1,752), including 18 new cases.
Ruff lint/format and mypy passed. No existing test assertions were changed.

## Controlled result

Both policies ran on `yyq@10.133.24.178` from clean commit `fab5a85`,
sequentially, on two d=5 blocks with five syndrome-extraction rounds each.
Only `cross_layer_reuse` differed; Device, fixed homes, all 240 measurement
outcomes, IDS budget and noise parameters matched.

| Metric | Off | On |
|---|---:|---:|
| Core compilation (s, single observation) | 195.562 | 23.969 |
| Transport duration (ms) | 48.417 | 77.533 |
| Atom loads / stores | 3440 / 3440 | 2320 / 2320 |
| Load batches | 230 | 385 |
| MOVE batches | 240 | 395 |
| Mean per-atom loss (%) | 1.480327 | 1.036875 |
| Maximum per-atom loss (%) | 2.015149 | 1.565065 |

Reuse reduces transfers and aggregate atom distance, but fragments strict
batches: average atoms per load batch falls from 14.96 to 6.03. Fewer transfers
do not imply shorter parallel transport. This version does not optimize
symmetry-preserving transport groups or guarantee a duration improvement.
Longer duration also increases dephasing exposure in the illustrative noise
model; lower loss is not a claim of lower total error.

Both results passed server/local semantic validation, stored-metric checks,
independent replay and noise reevaluation. Final layouts equal fixed homes.
Off-mode Atom IR and metrics exactly match the earlier `661cdf7` QEC result.

Inputs, results, reports and the two-case animation are in
`modules/physical_compiler/artifacts/qec-reuse-d5x2-r5-fab5a85/`.
The shared `artifacts/schedule-loss/index.html` has seven traces: the new two
first, followed by the five historical traces. Viewer analysis-model checks
passed; browser visual inspection remains pending.
