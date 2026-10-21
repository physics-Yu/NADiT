# 0027: Experiment specification, runner, and summaries

Status: Implemented on 2026-10-21; depends on ADRs 0023 to 0026.

## Context

The goal is to measure trade-offs between architectures, compilation
strategies, and noise assumptions over a fixed circuit set, and to let an AI
agent plan, run, and analyse these studies. ADR 0021 compiles one job;
`reference/benchmark.py` runs one pinned study with its own settings. Neither
expands a matrix, survives interruption, records failures as data, or
produces a table an agent can reason over.

A study of useful size, for example 6 architectures × 4 strategies × 26
circuits × 3 measurement cases, is about 1,900 compilations. It will run on
the group server, with some runs hitting search budgets.

## Decision

### Circuit suites

A suite is a directory `suites/<name>/` containing saved native QASM files and
`suite.json`:

```json
{"schema": "nadit.suite/1", "name": "paper26",
 "circuits": [{"id": "graphstate_60", "file": "graphstate_60.qasm",
               "sha256": "...", "qubits": 60}],
 "gate_set": ["rx", "ry", "rz", "cz"], "source": "mqt.bench==2.1.0"}
```

Suites are generated once (for paper26, through `reference/benchmark.py
prepare_input`) and committed. Experiments reference suites only by name and
hash, never by generator.

### Experiment specification

```json
{
  "schema": "nadit.experiment/1",
  "name": "rigid-vs-flexible-gate-width",
  "suite": "paper26",
  "architectures": [
    {"family": "zoned/v1", "base": "architectures/zoned_base.json",
     "sweep": {"gate_zones[0].pairs_per_row": [10, 20, 40],
               "aod.motion": ["flexible", "rigid"],
               "aod.max_rows": [16, null]}}
  ],
  "strategies": {
    "ids_strict":  "strategies/ids_strict.json",
    "astar_strict": "strategies/astar_strict.json",
    "ids_relaxed": "strategies/ids_relaxed.json"
  },
  "cases": {"seeds": [0, 1, 2]},
  "cost": [{"model": "additive", "params": "cost_params/baseline.json"},
           {"model": "hsim-aqc", "params": "cost_params/hsim_baseline.json"}],
  "budget": {"timeout_s": 900, "placement.max_nodes": 1000000},
  "validate": "required",
  "store": "failures",
  "baseline": {"architecture": {"aod.motion": "flexible", "aod.max_rows": null},
               "strategy": "ids_strict"}
}
```

- `sweep` is a Cartesian product over parameter paths. `exclude` (a list of
  partial assignments) removes combinations. `zip` groups sweep parameters
  that vary together.
- A strategy file is the `strategy` member of a job. `entry` defaults to
  `initial_storage`.
- `budget` values override strategy options uniformly, so search effort is
  equal across the compared runs.
- `validate` must be `required`. A run counts only if ADR 0023 validation,
  including the pulse and timing checks, passes.
- `store` is `none`, `failures`, or `all`, and controls which full result
  documents are kept.

### Run identity and storage

Each run has an ID: the hash of
(architecture content hash, strategy hash, circuit sha256, case, budget,
code version). The code version is the git commit plus a dirty flag; a dirty
tree is allowed only with `--allow-dirty` and is recorded. An experiment
directory `experiments/<name>/` contains:

- `experiment.json`: the spec, resolved, with its hash;
- `manifest.json`: code version, machine and CPU description, start time,
  suite hash;
- `runs.jsonl`: one line per finished run;
- `results/<run_id>.json`: full results, per the `store` policy.

Resuming skips run IDs already present in `runs.jsonl`. A run whose code
version differs from the manifest is refused unless `--new-version` starts a
separate manifest section; versions are never silently mixed.

### Run record

```json
{"run_id": "...", "architecture": {...params...}, "architecture_hash": "...",
 "strategy": "ids_strict", "circuit": "graphstate_60", "case": {"seed": 0},
 "status": "ok", "reason": null,
 "metrics": {...ADR 0021...},
 "cost": {"additive": {...}, "hsim-aqc": {...}},
 "validation": {"ok": true, "failure_count": 0},
 "compile": {"wall_s": 12.4, "peak_rss_mb": 410, "search_nodes": 18234, "budget_hit": false},
 "provenance": {"job_hash": "...", "result_path": null}}
```

`status` is one of `ok`, `unsupported` (ADR 0025), `invalid_job`,
`compile_error`, `timeout`, or `validation_failed`. Every non-`ok` status is a
result of the study and is reported. It is never dropped or retried silently.

### Runner

- `nadit experiment plan SPEC` prints the expanded run count per axis and
  any unsupported combinations, without compiling.
- `nadit experiment run SPEC [--workers N]` runs each compilation in its own
  process. The timeout is enforced by killing the process. Cost models run
  in the parent after validation.
- `nadit experiment status DIR` shows counts by status and the estimated time
  remaining.

### Summaries

`nadit experiment summarize DIR` writes `summary.json` and `summary.csv`:

- grouped statistics per (architecture, strategy) over circuits and cases:
  median and interquartile range of duration, transfers, `log_fidelity`, and
  compile time, plus status counts;
- per-circuit ratios against the declared `baseline` (geometric mean over
  circuits for the group view);
- Pareto fronts over chosen objectives, by default (`duration_us`,
  `-log_fidelity`, `slm_sites`), per circuit and aggregated;
- the outliers worth inspecting: largest regressions against baseline, every
  `validation_failed` run, and every `budget_hit`, each with its run ID and a
  ready-to-run viewer command (ADR 0022).

Hardware-size metrics (`slm_sites`, `gate_pairs`, `aod_capacity`,
`zone_area_um2`) come from the Device snapshot and are added to each run
record.

### Agent workflow

An agent works only through specs and summaries:

1. read `nadit describe`;
2. write or modify an experiment spec;
3. run `experiment plan` and check the run count and unsupported cells;
4. run the experiment and read `summary.json`;
5. propose the next spec.

The manifest's code-version check prevents the agent from changing compiler
code within an experiment. Code changes start a new experiment version,
following agent.md.

## Alternatives

Extending `reference/benchmark.py` would keep its pinned reproduction role
mixed with exploratory studies. A database would add a dependency where
append-only JSONL and per-run files suffice at this scale. Running cost
models inside each worker would tie noise sweeps to recompilation.

## Consequences

Any study is reproducible from its spec, suite, and code version, and its
failures are part of its results. Storage stays small under the `failures`
policy. The agent's conclusions are bounded by the validator (ADR 0023) and
the cost model (ADR 0026). Both are named in every summary.

## Implementation order

1. Suite format and the paper26 suite. Accept: hashes match; all 26 circuits
   load.
2. Spec parsing, sweep expansion, run IDs, and `experiment plan`. Accept:
   counts for product, `zip`, and `exclude`; stable IDs across runs.
3. Process runner with timeout, statuses, JSONL, and resume. Accept: a forced
   timeout, an unsupported cell, and an invalid result each record their
   status; an interrupted run resumes without duplicates.
4. Cost integration and hardware-size metrics. Accept: re-running `cost` on
   stored results reproduces the recorded values.
5. Summaries, baseline ratios, Pareto fronts, and outliers. Accept: a small
   synthetic run table gives hand-checked medians, ratios, and fronts.
6. A first real study: flexible, flexible 16, and rigid 16 on paper26 with
   IDS strict and the additive model, documented as an example experiment.

## Implementation details

The runner uses immutable resolved run jobs, isolated subprocesses, and a
parent-side JSONL writer. Timeout covers worker startup, loading, compilation,
validation, and result serialization; `compile.wall_s` measures compilation
alone. Unavailable peak-RSS/search counters are null, not invented estimates.
Cost failures are recorded in the cost entry and do not erase compilation status.
Cost evaluation uses one bounded parent-process thread; the main thread keeps
checking worker deadlines while a model is running. Full results are retained
on cost failure under the failures retention policy.

Run IDs exclude noise parameters and output retention, so a cost sweep does
not create new compilation work. Saved results can be reevaluated with
`experiment cost DIR`; cost reports go in a separate append-only journal.
Resume skips every completed status. A trailing partial JSONL line is truncated
before append; malformed complete records are errors. The output directory has
a single-writer lock and each run ID occurs once in a version section.

The manifest freezes commit, tracked/untracked source content hash, resolved
input hashes, and suite hash. Code is checked before dispatch and after each
worker; a changed checkout stops the experiment. `--new-version` creates a
separate version directory. `--allow-dirty` records the exact content hash.
The lock is enforced by the runner; it cannot prevent edits in unrelated tools.

The existing checked archive supplies the exact paper26 QASM bytes (about
14 MB). A committed manifest pins all hashes; a materializer restores the
ignored QASM files from that archive and verifies them. No circuit regeneration
is part of experiment execution. A small committed feedback suite exercises
the full loop. A bounded paper26 study records timeouts and unsupported cells
as outcomes; its budget is stated separately from the paper's 900 seconds.

Baseline matching fixes circuit and measurement case and every architecture
dimension not explicitly selected by the baseline. Missing/ambiguous baselines
are reported, never chosen arbitrarily. Ratios use positive costs; quality
uses `-log_fidelity` (not ratios of negative logs). Pareto groups compare the
same circuit/case, and aggregate fronts require identical successful-case
coverage. Outliers without retained results receive a reproduction command,
not a viewer command pointing to a nonexistent file.

## Verification and example studies

The combined suite passes 1,610 tests (baseline 1,575), including forced worker
timeout, a slow parent cost with another worker still running, unsupported and
invalid jobs, compiler exceptions, injected validation failure, trailing-record
recovery, resume without duplicate IDs, post-hoc noise reevaluation, version
mismatch, writer locking, and hand-checked baseline/Pareto tables.

`examples/experiments/feedback.json` defines six real cases: the three motion/
capacity profiles and both explicit measurement outcomes. `paper26-bounded.json`
defines 78 runs with a 5-second whole-worker budget, two workers in the example
invocation, IDS strict, and the additive baseline. This is a bounded workflow
study, not the original 900-second paper evaluation. The fixed geometry has
7,373 storage sites and 20 pairs on one gate row. Reports are written under
`artifacts/experiments/`; their manifest identifies the frozen implementation.

Peak RSS and search-node counters remain unavailable (null). A*'s existing
explicit open-set-limit error and worker timeouts set `budget_hit`; successful
searches do not invent a counter. Non-zoned/multi-AOD families and full circuit
state simulation remain outside these ADRs.
