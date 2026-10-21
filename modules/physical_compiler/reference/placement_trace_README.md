# Isolated placement search observations

`placement_trace_reference.py` freezes one gate or storage stage at the original
pipeline's incoming placement. It builds the pinned C++ placer at `-O3` and adds
observation calls around unchanged score evaluation and search operations.
The existing placement component oracle and its extraction remain unchanged.

The output contains original ordered candidate jobs, discrete source/target
ranks, per-node group maxima, lookahead, deviation, g/h scores, and ordered
search events. IDS `push` events describe calls to the bounded queue, including
attempts the original queue subsequently discards. `pop`, `select`, and `goal`
events observe decisions actually made by the original search. A* pushes are
unbounded up to its original `maxNodes` check.

Before using an isolated trace, compare its final layout exactly with the saved
uninstrumented full pipeline stage. The diagnosis runs established that parity
for graphstate 60 A*, graphstate 120/180 IDS storage, and graphstate 200 IDS gate
placement. Runtime of this observer is not compiler-performance evidence.

```powershell
uv run --locked python -m reference.placement_trace_reference `
  --cache-dir "$env:TEMP/nadit-qmap-cd8ec913" `
  --cxx "$env:TEMP/nadit-qmap-cd8ec913/compiler-tool/ziglang/zig.exe" `
  --fixtures reference/placement_stage_fixtures.json `
  --output "$env:TEMP/nadit-qmap-cd8ec913/placement-diagnostics/graphstate_200_trace.json"
```

`placement_trace_candidate.py` extracts the same stage from saved full runs and
observes the real local candidate/search modules. Compare candidates first,
then the first selected-node difference and the scores for those alternatives.
This identifies changes before they propagate into later layouts and routing.

The graphstate 200 gate-stage diagnosis found an exact-priority queue difference
after source float32 arithmetic was restored. All ordered candidates and 7,768
shared g/h evaluations agreed exactly. The first 58 selected nodes agreed; at
selection index 58, the fourth IDS restart, the original queue selected path
`[112]` and the serial-ordered local queue selected `[29]`, both at priority
`45.29306983947754`. The first three goal paths and costs agreed, while the
fourth goal changed the selected layout. This isolates bounded-queue tie
behavior independently of candidate or scoring differences.

## Corrections and remaining observations

`placement_decision_evidence.json` records the first differing decisions with
input, original-source, translation-unit, and raw-observation hashes.
Graphstate 60 A* first selects option 37 upstream and 13 before the correction;
graphstate 120 IDS first selects 2 upstream and 0 locally. Ordered candidates
at these decisions agree. Upstream priorities are strictly different; the
previous double evaluation makes the alternatives equal.

The scorer now preserves binary32 expression boundaries, source summation
order, and sorted-rank deviation. IDS uses the original indexed paired heaps.
Graphstate 60 then matches 15 selections and 1,519 common score paths;
graphstate 200 matches 77 selections and 10,306 common score paths exactly.

Storage cases 120 and 180 match all 96 and 224 physical selections respectively.
Equal-distance candidate permutations remain. Case 120 has two different
candidate means and 3,395 last-bit g/h differences across 12,270 common nodes;
case 180 matches all 27,365 common-node scores. The trace comparison normalizes
option indices to atom/site assignments without changing either candidate list.

`placement_stage_fixtures.json` keeps all four original incoming states and
expected final layouts. `queue_reference.py` independently executes the
unchanged source bounded queue on seven operation streams; `queue_expected.json`
records the removal orders used by regression tests. Existing oracles remain
unchanged.

## Full-pipeline recheck

`placement_recheck_results.json` records 34 successful runs: the seven affected
pairs plus ten earlier controls, with the same native inputs and architecture.
The worker budget is 120 seconds for this correctness recheck; the earlier
30-second benchmark report is preserved. All 17 placement sequences agree.
Fifteen pairs also agree through routing and Atom IR.

| Input and variant | Prior local atom loads | Fixed local / source loads | Fixed local / source transport (us) | Complete trace |
| --- | ---: | ---: | ---: | --- |
| 60 A* strict | 226 | 226 / 226 | 16581.924 / 16581.924 | Matches |
| 120 IDS strict | 434 | 440 / 440 | 27257.918 / 27257.918 | Matches |
| 120 IDS relaxed | 434 | 440 / 440 | 26832.040 / 27108.708 | Routing differs |
| 180 IDS strict | 660 | 662 / 662 | 45587.943 / 45587.943 | Matches |
| 180 IDS relaxed | 660 | 662 / 662 | 44849.143 / 44780.713 | Routing differs |
| 200 IDS strict | 758 | 756 / 756 | 51216.137 / 51216.137 | Matches |
| 200 IDS relaxed | 758 | 756 / 756 | 50633.198 / 50633.198 | Matches |

For 120 relaxed, the first differing transition is routing index 2; source
batch 7 includes atom 44 and the local batch does not. For 180 relaxed, the
first differing transition is index 4. On both transitions, pure strict and
relaxed grouping matches C++; production differs after staged Device/occupancy
admission. `relaxed_pipeline_divergence.json` records the exact source moves:
global action 269 activates intersection (171,309), occupied by stationary
atom 92; global action 757 activates (231,309), occupied by atom 175.
The source moves and local templates produce identical active grids, and
capacity/spacing/workspace all pass. Source atom-addressed code generation
emits these moves, while the existing Device semantics reserve every active
row/column intersection. This model difference is separate from placement.
Routing was not changed here.

`artifacts/source-placement-diagnosis-2026-10-21.zip` and
`artifacts/source-placement-recheck-2026-10-21.zip` preserve raw observations,
inputs, and full rerun traces. Hashes and CRC checks are recorded in the two
compact reports. Archives are ignored by Git.
