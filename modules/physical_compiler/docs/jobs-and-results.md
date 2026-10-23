# Jobs and saved results

See the [configuration reference](configuration-reference.md) for the complete
architecture, Device, compiler, and experiment field catalogue, including entry
providers, defaults, supported combinations, and complete caller examples.

Run these commands from `modules/physical_compiler`:

```text
uv run --locked nadit compile examples/jobs/multipair.json -o result.json --validate --text program.txt
uv run --locked nadit validate result.json
uv run --locked nadit validate result.json --device configs/multipair_flexible.json
uv run --locked nadit compare result.json another-result.json
uv run --locked nadit view result.json -o artifacts/result-viewer/index.html
```

`compile` prints the result JSON and optionally saves the same document with
`-o`. `--text` exports the Stim-style display. Version-2 results embed the
Device and native DAG: validation and playback need neither input file.
`validate --device` explicitly replaces the Device snapshot and records the
absolute path in `device_override`. The viewer accepts multiple positional result
arguments and its own optional `--device` JSON override.

## Job inputs

The saved examples are [pair.json](../examples/jobs/pair.json) and
[multipair.json](../examples/jobs/multipair.json). Both use `nadit.job/1`.
Edit a job and load it again to construct another compilation configuration.

| Field | Meaning |
| --- | --- |
| `circuit.qasm` | OpenQASM 2 or 3 file, relative to the job directory |
| `circuit.gate_set` | Native basis; defaults to RX/RY/RZ/CZ before DAG construction |
| `device` | Device JSON path relative to the job directory, or an inline object |
| `aod` | Selected Device AOD ID |
| `gate_zones` | Ordered zone IDs and ordered pairs of SLM site IDs |
| `strategy` | One explicit `kind` and its options for each of the five stages |
| `entry` | Automatic initial storage or explicit atom/site/qubit bindings |
| `case` | FakeBackend seed (default 0) and source measurement overrides (default empty) |

`gate_zones: []` is supported when the selected stages need no catalogue.
`max_cz_gates: "catalog"` resolves to its capacity. No other string selects an
automatic parameter value. Unknown fields and kinds report their JSON paths.

Automatic entry uses `{"kind":"initial_storage","atom_ids":null}`. An
explicit entry uses triples in `atoms`:

```json
{"kind":"explicit","atoms":[[7,"storage_r0_c0",0],[11,"storage_r0_c1",1]]}
```

Each triple is `[atom_id, slm_site_id, physical_qubit_index]`. Declaration
order is preserved; qubit binding uses the third element.

`case.overrides` maps source measurement IDs to bits, for example `{"12":1}`
for the supplied multipair circuit. These IDs are `ResultRef.measurement_id`,
not the native DAG node IDs used in Atom IR `source_ids`. Loading a job does
not sample measurements; the compiler retains its existing feedback handling.

## Registered stages

| Stage | Kinds | Options |
| --- | --- | --- |
| scheduler | `ready` | `max_cz_gates` (1 or explicit `"catalog"`) |
| scheduler | `source_asap` | `max_filling_factor` (0.9); existing static-circuit rules |
| placement | `pair` | None |
| placement | `multipair` | `storage_zone_id` (`"storage"`) |
| placement | `ids`, `astar` | `PlacementConfig` fields, `storage_zone_id`, optional `geometry` |
| transport | `pair`, `sequential`, `strict` | None |
| transport | `staged`, `relaxed` | Optional `geometry`; relaxed also has `prefer_split` (1.0) |
| emitter | `layer`, `whole_layer` | Whole-layer optional `coverage`; otherwise catalogue coverage |
| reuse | `none`, `matching`, `source` | None |

IDS and A* defaults come from `PlacementConfig.for_method(kind)`. A* uses
deepening factor 0.8, deepening value 0.2, and lookahead factor 0.2. Paper
evaluation overrides must be supplied explicitly. `method`, if supplied,
must agree with `kind`.

Explicit placement geometry uses `storage_arrays` and paired
`entanglement_arrays`. Each array has `id`, `kind`, `pitch: {x,y}`, a `sites`
matrix of Device site IDs, and `partner_sites` (default empty). Explicit
transport geometry uses `sites`, mapping Device site IDs to
`SiteStagingGeometry` fields, and `coordinate_quantum` (default null).
`geometry: null` retains the corresponding implementation's inference.

Explicit pulse coverage uses the result codec format:

```json
{"kind":"region","region":{"x_min":0,"y_min":20,"x_max":40,"y_max":30}}
```

A union uses `{"kind":"regions","regions":[...]}`, where each item is a
plain rectangle with the same four coordinate fields.

## Result documents

The CLI writes `nadit.result/2`. It retains the resolved job, provenance,
structured Atom IR, entry and exit layouts, operation records, measurement
records, layers, metrics, and optional validation from version 1, and adds:

| Field | Content |
| --- | --- |
| `device` | Expanded Device snapshot, accepted by `loads_device` |
| `dag` | Native operations, conditions, source/result identities, edges, wire counts, registers, and global phase |
| `inputs` | `qasm_sha256` and `device_sha256` captured when the job is loaded |

Paths in the resolved job remain absolute and strategy defaults are explicit.
Nested data is copied into the result. QASM identity hashes its exact file
bytes. Device identity hashes its expanded canonical JSON, so changing JSON
formatting alone does not change it. Canonical JSON sorts keys, uses compact
separators, and rejects nonfinite numbers.

`provenance.job_hash` replaces the QASM and Device references with those
content hashes before hashing the resolved job. Identical jobs in different
directories have the same identity. Provenance also records package, Python,
and Qiskit versions, the available git revision, and `git_dirty`. Dirtiness
covers tracked index/worktree changes; untracked files are excluded, and
unavailable git information is null.

`decode_result` reads both versions. Version-1 results have no DAG or Device
snapshot: CLI validation uses `job.device` (or `--device`) and reports
`semantic: "unavailable"`. Direct `encode_result` calls that omit `dag`,
`device`, and `inputs` continue to produce version 1; supply all three for
version 2. A job still needs its referenced files when compiled again.

The public Python modules are:

```python
from nadit_physical_compiler.job import compile_job, load_job
from nadit_physical_compiler.serialization import (
    decode_dag,
    decode_layout,
    decode_result,
    encode_result,
)
from nadit_physical_compiler.validation import validate_result

job = load_job("examples/jobs/multipair.json")
result = compile_job(job)
report = validate_result(
    result, entry_state=job.entry_state, device=job.device, dag=job.dag
)
document = encode_result(
    result,
    entry_state=job.entry_state,
    job=job.document,
    dag=job.dag,
    device=job.device,
    inputs=job.inputs,
    validation=report,
)
restored = decode_result(document)
entry = decode_layout(document["entry_state"])
dag = decode_dag(document["dag"])
```

`visualization.build_result_trace(document)` uses the embedded version-2
Device. Pass `device_document` explicitly to override it or to supply the
Device for a version-1 document. `write_html([trace], path)` writes the offline
viewer. Direct Python composition and `build_trace` remain available.

## Metrics and diagnosis

| Metric | Definition |
| --- | --- |
| `atom_steps` | Number of Atom IR steps |
| `program_duration_us` | Sum of each step's maximum action duration |
| `duration_us` | Completion time including final measurement availability |
| `action_counts` | Action counts by class name |
| `cz_gates_per_pulse` | Number of source gates assigned to each CZ pulse |
| `load_batch_sizes` | Number of atoms in each Load |
| `atom_load_count`, `atom_store_count` | Total atom transfers in the respective direction |
| `max_parallel_atoms` | Largest target count in one Move |
| `transport_duration_us` | Sum of Load/Move/Store action durations |

Transport duration retains the example's action-sum definition. Concurrent
transport actions contribute separately; program duration uses step timing.

Validation runs only when requested. With a DAG it checks these groups:

| Group | Checks |
| --- | --- |
| `motion` | Load/Move/Store against the common step-entry layout |
| `coverage` | Emitted/skipped native operation coverage and executable actions |
| `replay` | Replayed exit layout agrees with the recorded exit |
| `pulse` | CZ interaction edges match source gates; no spectators, disallowed holder pairs/zones, or concurrent movement through illumination |
| `addressing` | Source gates, parameters, atoms, channels, zones, holders, measurement bindings, and concurrent readout spacing |
| `timing` | Dependencies, measurement completion and feedback latency, recorded intervals, and condition decisions |

The detailed report contains `ok`, `failure`, `failures`, `failure_count`,
`checks`, and `semantic`. `failure` is the first failure; `failures` retains
at most 20 details while `failure_count` counts all findings. Each detail has
`step`, `action`, `kind`, `rule`, and `message`. Indices are zero-based;
whole-result failures have null indices. Checks continue after a finding.
If replay cannot reconstruct a layout, independent timing and coverage checks
continue, but subsequent spatial checks cannot run from that missing state.

`validate_result(..., dag=dag)` returns the detailed report with
`semantic: "checked"`. The original call without `dag` retains the compact
`{"ok": ..., "failure": ...}` report; `detailed=True` requests the extended
structural report with `semantic: "unavailable"`. The CLI always uses detailed
reports. Spatial checks use `COORDINATE_TOLERANCE`; timing uses
`TIME_TOLERANCE_US = 1e-9`. Point-addressed CZ coverage has zero geometric
width; no beam width is inferred for its straight-segment crossing check.

`compare` reports changed metrics and the first differing layer, program
field, operation, measurement, layout, DAG, Device, or input hash. Numeric
metric deltas are right minus left. It ignores job/provenance differences
when deciding equality. A successful comparison returns 0 even when results
differ.

CLI exit codes are 0 (success), 1 (invalid input or output path),
2 (compilation failure), and 3 (validation failure). Data goes to stdout as
JSON; diagnostics go to stderr.
