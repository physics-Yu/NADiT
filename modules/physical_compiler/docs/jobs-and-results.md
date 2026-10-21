# Jobs and saved results

Run these commands from `modules/physical_compiler`:

```text
uv run --locked nadit compile examples/jobs/multipair.json -o result.json --validate --text program.txt
uv run --locked nadit validate result.json
uv run --locked nadit compare result.json another-result.json
uv run --locked python -m examples.visualize_compilation --result result.json --output artifacts/result-viewer/index.html
```

`compile` prints the result JSON and optionally saves the same document with
`-o`. `--text` exports the Stim-style display. `validate` and the saved-result
viewer read the saved actions and Device without loading QASM or compiling.
The viewer accepts repeated `--result` arguments and an optional `--device`
JSON path when a Device file has moved.

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

`nadit.result/1` stores the resolved job, provenance, complete structured Atom
IR, entry and exit layouts, operation records, measurement records, layers,
metrics, and optional validation. Paths are absolute and strategy defaults
are explicit. Nested job and metadata values are independent snapshots.
Provenance includes a canonical job hash, package/Python/Qiskit versions, and
the available git revision. Keep referenced QASM and Device files with a job
when moving it between machines; a saved result needs only its Device to replay.

The public Python modules are:

```python
from nadit_physical_compiler.job import load_job, compile_job
from nadit_physical_compiler.serialization import (
    encode_result,
    decode_result,
    decode_layout,
)
from nadit_physical_compiler.validation import validate_result

job = load_job("examples/jobs/multipair.json")
result = compile_job(job)
report = validate_result(result, entry_state=job.entry_state, device=job.device)
document = encode_result(
    result, entry_state=job.entry_state, job=job.document, validation=report
)
restored = decode_result(document)
entry = decode_layout(document["entry_state"])
```

`visualization.build_result_trace(document, device_document)` consumes the two
public JSON formats. `write_html([trace], path)` writes the offline viewer.
The existing direct Python composition and `build_trace` interfaces remain.

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

Validation checks Load/Move/Store against the common step-entry layout,
replays the program, and checks emitted/skipped native operation coverage.
Its first failure contains `step`, `action`, `kind`, `rule`, and `message`.
Indices are zero-based; whole-result failures have null indices. Existing
motion rule names are preserved. Validation runs only when requested.

`compare` reports changed metrics and the first differing layer, program
field, operation, measurement, or layout record. Numeric metric deltas are
right minus left. It ignores job/provenance differences when deciding
execution equality. A successful comparison returns 0 even when results differ.

CLI exit codes are 0 (success), 1 (invalid input or output path),
2 (compilation failure), and 3 (validation failure). Data goes to stdout as
JSON; diagnostics go to stderr.
