# Source benchmark and ablation runner

`examples/source_benchmark.py` prepares the 26 circuits listed in the pinned
`eval/na/zoned/eval_ids_relaxed_routing.py`, then executes A* strict, IDS strict,
and IDS relaxed on both the original C++ pipeline and the local compiler.
Every variant consumes the same saved native operation list. The accompanying
QASM and input hashes make preprocessing inspectable.

## Inputs and settings

- Source revision: `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`.
- Original square architecture: 73 by 101 storage sites; 10 by 34 gate pairs;
  storage pitch 4 um; gate-array pitch 12 by 10 um; pair separation 2 um.
- AOD capability: 100 rows, 100 columns, minimum spacing 2 um.
- Preprocessing: `mqt.bench==2.1.0`, independent level; CZ/ID/U1/U2/U3;
  Qiskit optimization level 3, transpiler seed 0; remove measurements/barriers.
- The shared native adapter keeps U1 as source P (local native RZ), and
  represents U2 as U(pi/2, phi, lambda). Source CodeGenerator emits the same
  LocalU parameters for U2 and this U form. None of the 26 generated inputs
  contains ID. The QASM preserves the preprocessing's original gate names.
- The external environment uses the source script's dependency cutoff
  `2025-12-16T12:59:59Z`. Preparation records resolved package versions.
- A* evaluation settings: deepening factor 0.9, value 8.0, lookahead 0.2,
  maximum open set 10,000,000. The source class factory defaults are instead
  0.8, 0.2, and 0.2; `PlacementConfig.for_method("astar")` returns that factory
  configuration. Benchmark settings explicitly apply the evaluation overrides.
- IDS: factor 0.01, value 0, lookahead 0.4, 4 trials, queue capacity 100.
- Shared settings: filling factor 0.9; window enabled, width 16, ratio 1,
  share 0.8; reuse level 5; relaxed prefer-split 1.
- Movement: longest Euclidean displacement in each MOVE, followed by
  `200*(d/110)**(1/3)` for `d <= 110`, otherwise `200+(d-110)/1.1`.
  LOAD and STORE each take 15 us.

The local `Timing(..., move_profile="paper")` selects this distance function.
The existing linear profile remains the default. The choice affects action
duration and transport cost; placement proxy scores retain the source's
square-root distance formula.

## Execution

Create the paper environment outside the repository:

```powershell
uv venv --python 3.12 C:/Temp/nadit-paper-env
uv pip install --python C:/Temp/nadit-paper-env/Scripts/python.exe `
  mqt.bench==2.1.0 mqt.qmap==3.5.0 --exclude-newer 2025-12-16T12:59:59Z
```

Build the C++ pipeline with
`pipeline_reference.build_reference(cache, cxx, sources, optimization=3)`.
The return value includes the executable, full compiler command, and
translation-unit hash. Keep these with the result manifest.

From `modules/physical_compiler`:

```powershell
uv run --locked python examples/source_benchmark.py `
  --output-dir C:/Temp/nadit-paper-study `
  --paper-python C:/Temp/nadit-paper-env/Scripts/python.exe `
  --binary C:/Temp/nadit-reference/standalone-pipeline/pipeline-o3.exe `
  --workers 2 --timeout 900
```

`--prepare-only` saves the shared inputs without running compilers.
`--run-only` consumes existing inputs. `--cases graphstate_60` selects
individual original cases. `--engines` and `--variants` select comparisons.
Completed records are preserved on resume; `--rerun` explicitly replaces the
selected records. Timed-out Windows workers terminate their C++ child too.

The default limit is the source script's 900 seconds. A shorter observation
budget is an execution setting, recorded per run, and must not be reported as
the original paper's timeout outcome.

## Evidence

The output directory contains:

- `inputs/`: exact shared native JSON, QASM, hashes, and dependency versions.
- `runs/`: successful raw source/local stage traces and operation metrics.
- `manifest.json`: preparation status, every attempted run, elapsed time,
  failure or timeout, metrics, and the first differing pipeline stage.

Rearrangement time contains LOAD, MOVE, and STORE only. It is separate from
total Atom IR duration and compilation wall time. C++ per-stage timing excludes
JSON observation overhead; architecture preprocessing is reported separately.
Local compilation includes constructing Device/DAG records and compiling Atom
IR. Python and the standalone C++ adapter are different runtimes; their wall
times are not a reproduction of published release-build speedup ratios.

The full matrix contains 26 circuits and 156 implementation/variant runs.
Matching scores or transport totals alone do not establish identical
compilation. Stage comparison checks scheduling, reuse, placement, routing,
then actions and reports the first mismatch.

The historical study below predates the scorer and IDS queue corrections in
[the decision-level comparison](placement_trace_README.md). A* still uses
generation-order priority ties; the original `std::priority_queue` does not
specify equal-priority order. A* with this weighted heuristic does not establish
optimality.

The checked-in `benchmark_results.json` records the completed bounded study.
Raw native inputs and large traces stay in the study output directory.
The local ignored archive `artifacts/search-smarter-2026-10-21.zip` also preserves
all 26 native inputs, QASM files, 61 successful traces, the manifest, and the
reuse ablation. Its SHA-256 is recorded in the compact JSON; archive integrity
and all 52 input-file hashes were verified.

## Recorded study: 2026-10-21

Implementation commit: `1d8478a`. The original C++ adapter was built with
Zig 0.13.0, C++20, `-O3`, and translation-unit SHA-256
`88ba43ecf50d75ed256967b202137ec522c995659046f06914cd99dfa36c668c`.
The explicit paper Rydberg range `[0, 308]` to `[400, 400]` is supplied to
both implementations, separately from the trap-site rectangle.

All 26 inputs were prepared and all 156 runs were attempted, with two workers
and a **30-second whole-worker budget**. No run remains pending. This budget
includes architecture setup, observation and trace serialization; it is shorter
than the paper's 900-second compilation limit.

| Implementation | Variant | Successful | Timed out |
| --- | --- | ---: | ---: |
| Original C++ | A* strict | 8 | 18 |
| Original C++ | IDS strict | 18 | 8 |
| Original C++ | IDS relaxed | 18 | 8 |
| Local | A* strict | 1 | 25 |
| Local | IDS strict | 8 | 18 |
| Local | IDS relaxed | 8 | 18 |

There were 61 successes and 95 worker timeouts, with no exception failures.
Seventeen source/local pairs completed on both sides. Ten pairs matched through
the entire action trace: IDS strict and relaxed for graphstate sizes 60, 80,
100, 140, and 160. Seven pairs first differed in placement:

| Input | Variants | First differing placement |
| --- | --- | --- |
| graphstate 60 | A* strict | First CZ execution layout |
| graphstate 120 | IDS strict, IDS relaxed | Storage layout after the second CZ layer |
| graphstate 180 | IDS strict, IDS relaxed | Storage layout after the second CZ layer |
| graphstate 200 | IDS strict, IDS relaxed | Fourth CZ execution layout |

Scheduling and reuse matched before each difference. At the time of this
study, the first different search decision had not been located. The later
decision-level comparison records the causes and the separate recheck.

The complete JSON records each attempted run, native-input and QASM hashes,
source hashes, metrics, first differing paths, source/local metric differences,
and successful within-implementation ablations. For graphstate 60:

| Implementation | Variant | Atom loads (= stores) | LOAD actions (= STORE actions) | Rearrangement time (us) |
| --- | --- | ---: | ---: | ---: |
| Original C++ | A* strict | 226 | 58 | 16581.924 |
| Local | A* strict | 226 | 59 | 15960.838 |
| Original C++ / Local | IDS strict | 204 | 46 | 12884.994 |
| Original C++ / Local | IDS relaxed | 204 | 46 | 12794.416 |

The A* layouts differ, so the two A* durations describe different generated
plans. The IDS strict/relaxed action traces match their corresponding original
runs.

A separate small reuse-policy ablation uses
`source_endpoint_priority_reuse` with the same paper timing. Original C++ and
local source reuse match: 14 atom loads and stores, 5 LOAD and STORE actions,
1706.039 us. The complete-shared-edge policy selects `{1, 2}` instead of `{0}`:
12 atom loads and stores, the same 5 LOAD and STORE actions, and 1710.387 us.
Fewer loaded atoms did not reduce rearrangement time in this example.

The paper's 900-second evaluation and published speedup ratios have not been
reproduced by this bounded study. The unresolved placement differences and
larger local cases remain work for subsequent source alignment and profiling.
