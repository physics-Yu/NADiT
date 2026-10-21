# 0021: Job specification and result document

Status: Implemented on 2026-10-21.

## Context

The physical compiler is a Python library. A compilation is defined by code
that constructs a Device, a GatePairCatalog, a PlacementGeometry, an entry
layout, a scheduler, a placer, a transporter, an emitter, an optional reuse
analyzer, and a FakeBackend. Device configuration is JSON; strategy
configuration is not. `examples/compile_multipair.py` exposes about fifteen
argparse options to assemble one family of strategies, and
`reference/benchmark.py` builds its own settings separately.

Results are dataclasses. `AtomProgram.to_text()` produces Stim-style text with
no parser. Metrics such as load counts, transport time, and CZ gates per pulse
are computed inside the example script. Replay and the reference checks in
`motion.py` exist, but no single call states whether a saved result is legal
and, if not, where the first violation occurs.

Researchers and AI agents both need to state a compilation, run it, compare
variants, and diagnose a failure without writing composition code or
parsing free text. A future viewer should read results without importing
compiler internals.

## Decision

Add a declarative job specification, a strategy registry, a versioned result
document, a validation report, and a command-line entry point. The existing
Python API remains the primary interface; the new layer composes it.

### Job specification

A job is a JSON document with schema `nadit.job/1`:

```json
{
  "schema": "nadit.job/1",
  "circuit": {
    "qasm": "examples/multipair_feedback.qasm3",
    "gate_set": ["rx", "ry", "rz", "cz"]
  },
  "device": "configs/multipair_flexible.json",
  "aod": "transport",
  "gate_zones": [
    {"zone": "entanglement",
     "pairs": [["gate_0_left", "gate_0_right"], ["gate_1_left", "gate_1_right"]]}
  ],
  "strategy": {
    "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
    "placement": {"kind": "ids", "trials": 4, "queue_capacity": 100},
    "transport": {"kind": "relaxed", "prefer_split": 1.0},
    "emitter": {"kind": "whole_layer"},
    "reuse": {"kind": "matching"}
  },
  "entry": {"kind": "initial_storage", "atom_ids": null},
  "case": {"seed": 17, "overrides": {"12": 1}}
}
```

- Relative paths resolve against the job file's directory. `device` may also
  be an inline Device object accepted by `loads_device`.
- Each strategy stage has a `kind` and that implementation's options. Option
  names match the constructor or Config dataclass field names. Unknown keys
  are errors, not ignored.
- `"catalog"` resolves `max_cz_gates` to the catalogue capacity. No other
  implicit values are introduced.
- `entry.kind` is `initial_storage` (`make_initial_layout`) or `explicit`
  (atom ID, SLM site ID, physical qubit triples).
  Explicit entries use `"atoms": [[7, "storage_r0_c0", 0], ...]`;
  the last element is the physical qubit index, not the atom ID.
- `case.overrides` keys are stable source measurement IDs, matching
  `ResultRef.measurement_id`. Values are 0 or 1.
- Branch sweeps and benchmark matrices are outside this schema; a caller
  generates several jobs.

An empty `gate_zones` list is accepted when the selected stages do not use a
catalogue. Geometry inference runs only when a selected module or automatic
entry layout needs it. Explicit geometry retains the existing constructor
semantics. Result documents snapshot the resolved job and supplied metadata
so later edits to a caller's configuration cannot change a saved result hash.

### Strategy registry

`registry.py` maps stage kinds to existing implementations:

| Stage | Kinds |
| --- | --- |
| scheduler | `ready`, `source_asap` |
| placement | `pair`, `multipair`, `ids`, `astar` |
| transport | `pair`, `sequential`, `strict`, `staged`, `relaxed` |
| emitter | `layer`, `whole_layer` |
| reuse | `none`, `matching`, `source` |

`astar` uses `PlacementConfig.for_method("astar")` before applying options.
The registry validates options against each Config dataclass and reports
the job path of an invalid field, for example
`strategy.placement.queue_capacity`. It constructs objects; it adds no
algorithmic behaviour. New implementations register one entry.

### Result document

`nadit.result/1` is a JSON document containing:

| Field | Content |
| --- | --- |
| `job` | The resolved job, with defaults filled and paths made absolute |
| `provenance` | Package version, git commit when available, job hash, Python and Qiskit versions |
| `program` | Structured steps and actions with every Atom IR field; `global_phase` |
| `entry_state`, `exit_state` | Atom positions, holders, AOD grids, and qubit binding |
| `operations`, `measurements`, `layers` | `CompilationResult` records |
| `metrics` | Fixed set listed below |
| `validation` | Present when requested; see below |

`serialization.py` provides `encode_result` and `decode_program`/
`decode_layout`. Decoding reconstructs equal dataclasses, so a saved result
can be replayed and validated without recompiling. `to_text()` remains the
human-readable view and is not a storage format.

`metrics.py` moves the example's computations into the library:
`atom_steps`, `program_duration_us`, `duration_us`, `action_counts`,
`cz_gates_per_pulse`, `load_batch_sizes`, `atom_load_count`,
`atom_store_count`, `max_parallel_atoms`, and `transport_duration_us`.
Examples and the benchmark runner use this module.

### Validation report

`validation.py` checks a program from its entry state:

- each Load, Move, and Store against the layout before its step, using the
  existing `check_load`, `check_move`, and `check_store`;
- replay of the full program against the recorded exit state;
- source-operation coverage: every emitted operation appears in action
  `source_ids`, and no skipped operation does.

The report records `ok` and the first failure as
`{"step": i, "action": j, "kind": "MOVE", "rule": "...", "message": "..."}`,
reusing `ConstraintResult.rule`. Validation stays a reference tool outside
the default construction path, as in ADR 0008.

### Command line

`cli.py` exposes a console script `nadit`:

```text
nadit compile JOB.json [-o RESULT.json] [--validate] [--text PROGRAM.txt]
nadit validate RESULT.json
nadit compare A.json B.json
```

`compare` prints metric differences and the first differing layer, step, or
action. Output is JSON on stdout and diagnostics on stderr. Exit codes are
0 for success, 1 for an invalid job, 2 for a compilation error, and 3 for a
validation failure. Invalid result files and output paths also return 1.
The CLI uses only the standard library.

`compile` always prints the result JSON; `-o` also saves that document.
`validate` reads the saved program, entry state, and Device without reopening
the circuit. `compare` reports `equal`, changed `metrics`, and
`first_difference`. Numeric metric deltas are right minus left. Comparison
checks layers and program before operation, measurement, entry, and exit
records; job and provenance differences do not imply different execution.

### Out of scope

A visual viewer and an MCP or other agent-facing server are separate
decisions. Both consume `nadit.result/1` and the Device JSON and must not
import compiler internals.

## Alternatives

Extending each example's argparse options keeps configuration in scripts and
cannot be shared by the benchmark or by other callers. Pickle or dataclass
reprs are not stable across code changes and cannot be read outside Python.
Parsing `to_text()` would make a display format a storage contract. A global
options object would couple independently replaceable stages, which ADR 0008
rejected; the registry instead keeps options per stage.

## Consequences

A compilation is reproducible from one file, and variants differ by a small
JSON diff. AI agents and scripts can write jobs and read structured results
and failures. Adding the console script changes `pyproject.toml`
(`[project.scripts]`) but adds no dependency. Schema changes require a new
version string; `nadit.job/1` readers reject other versions.

The registry duplicates constructor parameter names in its validation, so a
renamed option must update both. Tests guard this by constructing every
registered kind from a minimal job.

## Implementation order

Each step is one branch with tests and a full-suite run against the baseline.

1. `serialization.py`: encode and decode Atom IR, layouts, and result records.
   Accept: round-trip equality for every action type, AOD grids, and the
   existing example programs.
2. `metrics.py`: move the example computations. Accept: example output is
   unchanged.
3. `validation.py`. Accept: compiled examples pass; hand-built illegal moves,
   ghost intersections, and missing coverage report the expected first failure.
4. `job.py` and `registry.py`. Accept: jobs equivalent to
   `compile_pair.py` and `compile_multipair.py` (each transport and placement
   option) produce results equal to the scripts' Python composition; invalid
   kinds and unknown keys report their job paths.
5. `cli.py` and the console script. Accept: subprocess tests for each command
and exit code; `compare` locates a deliberately changed option's first
   difference.
6. Update the module README, CHANGELOG, and convert the examples to load
   saved jobs where this does not change their output.

The implementation and commands are documented in
[Jobs and saved results](../../modules/physical_compiler/docs/jobs-and-results.md).
The existing viewer has a saved-result adapter under ADR 0022; it accepts
result and Device JSON without invoking the compiler.
