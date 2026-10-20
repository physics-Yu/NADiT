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

Local A* preserves the existing deterministic generation-order priority ties;
the original `std::priority_queue` does not specify an equal-priority order.
Local score arithmetic also retains the existing numerical representation.
Any resulting first-goal difference remains in the report; A* with the source's
weighted heuristic does not establish optimality.

The checked-in `benchmark_results.json` records the completed bounded study.
Raw native inputs and large traces stay in the study output directory.
