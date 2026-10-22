# 0033: Search Smarter fixed-reference QEC mode

Status: Implemented and verified, 2026-10-22.

## Contract

Add optional `strategy.mode`: `adaptive` preserves the existing Search Smarter
pipeline; `qec` restores every atom's position, SLM holder, and qubit binding
after each selected layer. CZ execution still uses the configured entanglement
zone, placement algorithm, and transporter. The reference layout may be supplied
by `entry.kind=explicit` or the existing initial-storage provider. Fixed homes
are a boundary condition, not a restriction on temporary gate arrangements.

QEC mode disables cross-layer retention and reuse lookahead. Existing strategy
files can enable the mode by adding one field; their resolved `reuse` becomes
`none`, even if the input requested matching/source reuse. Other placement,
transport, timing, and capacity settings remain configured as before.
The compiler retains sole ownership of DAG progress and measurement results.

## Implementation

`placement/reference.py` adapts a placer to return to its input layout and
suppresses reuse/following hints. For IDS/A*, it selects a reference-exit path
inside HeuristicPlacer: perform gate assignment, then skip storage candidate
generation and storage search. It does not search for an exit and discard it.
Pair/multipair placement reuse the same adapter. SearchSmarterStrategy wraps
its configured placer once when mode is qec and carries no persistent home
or measurement state; layer-by-layer restoration preserves the initial
reference inductively. Readout and local/global single-qubit layers also
return to their input layout.

The return destination is a direct transport target, not an extra round trip
through the adaptive storage result. Full validators remain outside planning.

## Selection

The existing five-stage object accepts optional `mode`. A named spelling is
also accepted:

```json
{
  "kind": "search_smarter",
  "mode": "qec",
  "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
  "placement": {"kind": "ids"},
  "transport": {"kind": "staged"},
  "emitter": {"kind": "whole_layer"},
  "reuse": {"kind": "none"}
}
```

Omitting kind remains supported. Omitting mode retains the previous behavior
and resolved-document shape. The Python constructor also accepts `mode="qec"`.
Discovery, job JSON Schema, and a ready-to-run explicit-home example expose
the same option. This is a targeted policy extension, not the full compiler
registry/experiment redesign proposed separately in ADR 0032.

## Verification

Exercise explicit, permuted atom/site bindings; repeated pairs; partner changes;
measurement branches and latency; layer-by-layer return and independent replay;
IDS/A* with strict/staged/relaxed transport; the existing rigid strict route;
local-operation relocation; and pair/multipair adapters. Prove the QEC path
does not invoke storage search or reuse analysis. Compare omitted/adaptive
mode programs exactly, and verify named/legacy job roundtrip and saved-result
CLI validation. Preserve existing test expectations.

Baseline: 1,666 tests passed. After implementation: 1,690 tests passed in
91.89 seconds, including 24 new fixed-reference cases. Ruff formatting, lint,
and mypy passed for src/tests/examples. The documented example compiles and
passes semantic validation; CLI roundtrip also validates its saved result.
No existing test assertions were changed.
