# Physical compiler configuration reference

This is the caller reference for projects, people, and AI clients using the
physical compiler. It documents the existing configuration interface; it does
not introduce a new placement or routing policy.

The four configuration categories are:

1. [Architecture](#architecture): generate zones, SLM sites, and paired arrays.
2. [Device](#device): describe geometry, capabilities, and physical timing.
3. [Compiler](#compiler): choose the circuit, entry layout, strategy, and case.
4. [Experiment](#experiment): run controlled products of those configurations.

[Complete examples](#complete-examples) and [caller workflow](#caller-workflow)
follow the field reference. Names and defaults below describe the current
implementation, not the parameter values of a particular research run.

## Conventions

- Coordinates, pitches, distances, and radii use micrometers (`um`). Durations
  use microseconds (`us`); experiment wall-clock budgets use seconds (`s`).
- A point/pitch is `{"x": number, "y": number}`. A rectangle is
  `{"x_min": number, "y_min": number, "x_max": number, "y_max": number}`.
- `required` means that the field must be supplied when its containing object
  is used. Omission and explicit `null` are different unless stated otherwise.
- Row/column counts are positive integers. AOD capacities and readout capacity
  accept `null` for no count limit. Geometry, spacing, and occupancy still apply.
- A site matrix is a nonempty rectangular array of arrays of site ID strings.
- Job file references resolve relative to the job file. Experiment references
  resolve relative to the experiment file; QASM paths inside a suite resolve
  relative to the suite manifest.
- Schema IDs: `nadit.job/1`, `nadit.result/2`, `nadit.experiment/1`,
  `nadit.suite/1`. Architecture family IDs have their own versions.
- Use schema field names exactly. Unknown job/strategy options are not extension
  points; a Python constructor parameter is not automatically a JSON option.

## Architecture

An architecture is a Device generator, not a compiler selector. A job embeds it
as `device.architecture = {"family": ..., "params": ...}`. A plain Device JSON
path is the other common entry point; it refers to an expanded/grid-based Device,
not to an architecture-family wrapper.

| Family | Geometry | Typical compiler |
| --- | --- | --- |
| `zoned/v1` | Separate storage and entanglement blocks; optional readout region | `search_smarter` |
| `shared/v1` | Shared double-trap cells; optional readout region | `nonzoned` |

Admission checks actual Device capabilities and array geometry rather than
equating a family name with a compiler. Merely allowing overlapping zones does
not select shared-array compilation.

### zoned/v1 parameters

| Field | Required/default | Meaning |
| --- | --- | --- |
| `name` | Generated from family and parameter hash | Device name |
| `storage` | Required, nonempty array | Storage blocks, described below |
| `gate_zones` | Required, nonempty array | Paired entanglement arrays |
| `measurement` | Omitted: no readout block | One readout region and its capability |
| `aod` | Defaults below | One generated AOD |
| `timing` | `"linear"` | `"linear"`, `"paper"`, or a complete Timing object |
| `cz` | Required | CZ capability; see Device |
| `local` | Omitted/null: absent | Addressed rotation/reset capability |
| `global_control` | Omitted/null: absent | Global Y-rotation capability |
| `shared_zones` | `[]` | Pairs of zone IDs permitted to overlap |
| `site_order` | Generated order | List every generated site ID exactly once to override order |

Each `storage[]` block:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `id` | Required | Zone ID |
| `origin` | Required | Absolute point |
| `rows`, `columns` | Required | Grid dimensions |
| `pitch` | Required | Positive horizontal/vertical site pitch |
| `prefix` | `id` | Generated IDs use `<prefix>_r<row>_c<column>` |
| `bounds` | Local `[0,0,columns*pitch.x,rows*pitch.y]` | Rectangle relative to this origin |
| `operations` | `["load","store","rotate","reset"]` | Allowed operations |

Each `gate_zones[]` block:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `id` | Required | Zone ID |
| `position` | Required | Offset from the **first storage origin** |
| `rows` | Required | Number of gate rows |
| `pairs_per_row` | Required | Number of pairs per row |
| `separation` | Required, positive | Horizontal offset from left to right trap |
| `pitch` | Required | Horizontal pair pitch and vertical row pitch |
| `array_id` | `id` | Generated pair-array ID |
| `bounds` | Local bounding rectangle of generated traps | Relative to the translated gate origin |
| `operations` | `["load","store","cz","rotate","reset"]` | Allowed operations |
| `left_prefix`, `right_prefix` | `<id>_left`, `<id>_right` | Generated site prefixes |
| `left_ids`, `right_ids` | Generated IDs | Explicit matrices with the configured dimensions |

The default gate bounds have local maxima
`((pairs_per_row-1)*pitch.x+separation, (rows-1)*pitch.y)`.
Explicit side IDs override naming by the corresponding prefix.

The optional `measurement` object:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `id` | `"measurement"` | Zone ID |
| `position` | Required | Offset from the first storage origin |
| `bounds` | Required | Rectangle local to the translated position |
| `operations` | `["measure"]` | Allowed operations |
| `readout` | Required | Readout capability, described under Device |

The optional `aod` object:

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | `"transport"` | AOD ID selected by the job |
| `motion` | `"flexible"` | `flexible` or `rigid` |
| `min_spacing` | `1` | Minimum active-line spacing |
| `max_rows`, `max_columns` | `null` | Independent line-count limits |
| `margin` | `10` | Nonnegative expansion of the combined zone bounds |
| `workspace` | Combined zone bounds expanded by margin | Explicit absolute rectangle overrides expansion |

Timing strings expand to `load_us=10`, `store_us=10`,
`move_us_per_um=1`, and the selected `move_profile`. To choose different transfer
times, supply the Timing object instead.

Generated roles are storage/entanglement/measurement according to block type.
Closed zone bounds may overlap only for declared `shared_zones` pairs. Sites
must lie within their regions and AOD workspace; gate pairs must fit the CZ
radius and remain isolated from other pairs. There is no built-in 100 um
gate-row spacing. Storage pitch, row pitch, and inter-zone offsets are explicit.

### shared/v1 parameters

The top-level fields are `name`, `shared_arrays`, `measurement`, `aod`, `timing`,
`cz`, `local`, `global_control`, and `shared_zones`. Only `shared_arrays` and `cz`
are required. There are no `storage`, `gate_zones`, or `site_order` fields.

`shared_arrays[]` uses all the gate-block fields above, with these differences:

- Array `position` and measurement `position` are **absolute**, not offsets from
  a storage block. Bounds remain local to each position.
- Default array operations are `["cz","rotate","reset"]`.
- Generated zone roles are unspecified (`null`); permissions describe their use.
- AOD, timing, naming, side matrices, and separation follow the same helpers and
  defaults as zoned/v1. Measurement is optional.

The family does not force `cz.allow_spectators=true`; the nonzoned compiler
requires it and checks it independently.

Sources: [architecture schema](../src/nadit_physical_compiler/architectures/schema.py),
[zoned generator](../src/nadit_physical_compiler/architectures/zoned.py),
[shared generator](../src/nadit_physical_compiler/architectures/shared.py).

## Device

Supply `job.device` as an expanded Device object or a Device JSON path to avoid
the architecture generator. All Device coordinates below are absolute.

### Geometry fields

| Field | Required/default | Content |
| --- | --- | --- |
| `name` | Required | Device name |
| `zones` | Required | Array of zone objects |
| `slm_sites` | `[]` | Individual `{id, position}` objects |
| `slm_grids` | `[]` | Grids `{prefix, origin, rows, columns, pitch}` |
| `pair_arrays` | `[]` | Arrays `{id, zone, left, right}` |
| `aods` | Required | AOD objects |
| `timing` | Required | Timing object |
| `cz`, `local`, `readout`, `global_control` | Omitted/null: absent | Capability objects below |

- A zone has required `id`, `bounds`, `operations`, and optional `role`.
  Role is `storage`, `entanglement`, `measurement`, or `null` (default).
  Operation permissions, not role alone, govern execution.
- Grid fields are all required. Generated site names use zero-based row/column
  indices. Explicit sites and generated grids contribute to the same site set.
- A pair array has required `id`, `zone`, `left`, and `right`. Each side is either
  a site-ID matrix or a complete grid object. Corresponding entries form pairs.
- An AOD has required `id`, `workspace`, `min_spacing`; optional `max_rows=null`,
  `max_columns=null`, `motion="flexible"`. `min_spacing` must be positive.
  Expanded Device AOD objects do not accept the generator's `margin` field.
- `flexible` preserves row/column order and shared-line relations. `rigid`
  requires whole-grid translation. Capacity does not change the motion model.
- The Device format permits multiple AODs, but a job selects one `aod` ID; this
  is not an interface to concurrent multi-AOD scheduling.
- There is no `slm_min_spacing` scalar. SLM geometry comes from positions/pitch;
  AOD line spacing and concurrent measurement spacing are separate constraints.

Storage discovery uses zones with `role="storage"`, falling back to the legacy
zone ID `storage` if there are no such roles.

### Timing and capabilities

| Object | Field | Required/default | Meaning |
| --- | --- | --- | --- |
| `timing` | `load_us` | Required | SLM to AOD transfer |
| | `store_us` | Required | AOD to SLM transfer |
| | `move_us_per_um` | Required | Linear distance coefficient |
| | `move_profile` | `"linear"` | `linear` or `paper` |
| `cz` | `channel` | Required | Pulse channel name |
| | `radius_um` | Required, positive | Interaction-distance threshold |
| | `duration_us` | Required | Pulse duration |
| | `holder_pairs` | All three combinations | Unordered pairs: SLM-SLM, SLM-AOD, AOD-AOD |
| | `allow_spectators` | `false` | Allow isolated illuminated atoms, never extra interactions |
| `local` | `channel` | Required | Addressed channel name |
| | `rotation_us`, `reset_us` | Required | Addressed rotation and reset durations |
| | `holders` | `["slm","aod"]` | Allowed holders |
| `readout` | `channel`, `duration_us` | Required | Readout channel and exposure duration |
| | `latency_us` | `0` | Result delivery delay after exposure |
| | `min_spacing_um` | `0` | Concurrent-readout minimum separation |
| | `max_atoms` | `1` | Positive integer or null; null removes the count limit |
| | `retain_atoms` | `true` | Whether atoms remain available after readout |
| | `holders` | `["slm","aod"]` | Allowed readout holders |
| `global_control` | `channel`, `rotation_us` | Required | Global Y-rotation channel and duration |
| | `holders` | `["slm","aod"]` | Allowed holders |
| | `region` | `null` | `[xmin,ymin,xmax,ymax]`; null uses the zones' bounding rectangle |

Durations and the linear movement coefficient are nonnegative. Movement time
for distance `d` is:

- `linear`: `move_us_per_um * d`.
- `paper`, `d <= 110`: `200 * (d / 110) ** (1 / 3)`.
- `paper`, `d > 110`: `200 + (d - 110) / 1.1`.

The paper profile does not use the linear coefficient. It is a distance-to-time
function, not a dynamics simulation.

Capabilities describe what is permitted, not what every compiler can exploit.
Current readout emission requires `retain_atoms=true`. Current catalogue-based
placement executes SLM-SLM CZ; there is no configuration objective that searches
over the three holder combinations. Channels and capability times are Device-wide,
not per-zone lists of independently schedulable beams.

Sources: [Device schema](../src/nadit_physical_compiler/schemas/device.py),
[Device implementation](../src/nadit_physical_compiler/device.py),
[capabilities](../src/nadit_physical_compiler/capabilities.py).

## Compiler

### Job envelope

| Field | Required/default | Meaning |
| --- | --- | --- |
| `schema` | Required: `"nadit.job/1"` | Input document version |
| `circuit.qasm` | Required | OpenQASM 2/3 input path |
| `circuit.gate_set` | `["rx","ry","rz","cz"]` | Native decomposition basis, before DAG construction |
| `device` | Required | Device path, Device object, or architecture wrapper |
| `aod` | Required | Selected Device AOD ID |
| `gate_zones` | Device `pair_arrays` | Ordered `{zone, pairs:[[left_site,right_site],...]}` objects |
| `strategy` | Required | Compiler selection and options |
| `entry` | Compiler's default provider | Initial placement |
| `case` | `{seed:0, overrides:{}}` | Measurement outcomes |

Explicit `gate_zones` replaces the automatic catalogue selection; it does not
add to it. An empty list is useful only for stages that need no catalogue.
Device sites are still required. The frontend basis is not a request to add new
Atom IR instructions: current emission supports rx/ry/rz/u/cz/global_ry,
measurement and reset; metadata/phase handling is separate.

### Compiler selection and entry

| `strategy.kind` | Default entry | Other entry | Public strategy options |
| --- | --- | --- | --- |
| `search_smarter` | `initial_storage` | `explicit` | Five stages, mode, cross-layer switch |
| `nonzoned` | `enola_initial` | `explicit` | `max_cz_gates` only |

Both registrations currently report version `1` and support feedback through
the common DAG controller. Omitting kind is the legacy Search Smarter spelling.
`enola_initial` is an entry provider, not a third compiler. Production external
whole-program compiler adapters are not currently registered.

| Entry kind | All additional fields | Semantics |
| --- | --- | --- |
| `explicit` | Required `atoms` | Triples `[atom_id, slm_site_id, physical_qubit_index]`; every physical qubit exactly once |
| `initial_storage` | `atom_ids:null` or integer list | Search Smarter automatic storage placement |
| `enola_initial` | `array`, `seed:0`, `iterations:100`, `moves_per_iteration:400` | One initial ENOLA SA run on a shared pair array |

`enola_initial.array` may be omitted only when the Device has one unambiguous
pair array. Iterations and moves per iteration are positive integers. This
provider uses left traps as homes and leaves right traps free. It previews the
dependency-known initial CZ region without sampling unknown measurement branches.
It does not enable dynamic SA between layers. A nonzoned explicit entry must
use declared shared cells with an empty AOD and legal pair occupancy.

`case.seed` is an integer, default 0. `case.overrides` maps string source
measurement IDs to bits 0/1, default `{}`. These are measurement identities,
not native DAG node IDs or classical-bit indices. `entry.seed` and `case.seed`
control different random processes. Measurement results remain owned by the
common controller and are sampled once per identity.

### Search Smarter modes

`scheduler`, `placement`, `transport`, `emitter`, and `reuse` are all required
objects, each with a required `kind`. Their optional parameters are listed below.

| Field | Default | Values |
| --- | --- | --- |
| `kind` | `"search_smarter"` | Explicit compiler identity |
| `mode` | `"adaptive"` | `adaptive` or `qec` |
| `cross_layer_reuse` | `false` | Boolean, true only in QEC mode |

- Adaptive keeps the selected placement/reuse behavior.
- QEC with the switch omitted/false restores each layer's reference and resolves
  `reuse.kind` to `none`, even if a different analyzer was written in the input.
- QEC with the switch true requires `matching` or `source`, a catalogue placer
  (`ids`, `astar`, `multipair`), and SLM homes outside CZ coverage. It retains
  selected atoms at gate sites across known CZ layers and intervening rotations.
  Existing lookahead ends at measurement, reset, unknown feedback or circuit end.
  Homes are restored at window boundaries and before measurement/reset, not
  necessarily at every barrier or CZ layer. This does not preserve complete
  surface-code geometry during the window or guarantee a speedup.

Discovery's short `modes.qec` text still describes the switch-off behavior;
the switch schema and the rules above cover the implemented exception.

### Scheduler

| Kind | All parameters/defaults | Restrictions |
| --- | --- | --- |
| `ready` | `max_cz_gates:1` | Positive integer or `"catalog"`; catalogue required for the latter |
| `source_asap` | `max_filling_factor:0.9` | Static circuit; no conditions, measurement or reset; regular/explicit arrays |

The source filling factor lies in [0,1]. Capacity is the sum over gate arrays
of `max(1, floor(factor * rows * columns))`. Ready-scheduler measurement capacity
comes from `Device.readout.max_atoms`, not another JSON scheduler option.

### Placement

| Kind | All parameters | Restrictions |
| --- | --- | --- |
| `pair` | None | At most two physical qubits |
| `multipair` | `storage_zone_id` | One selected storage zone and a gate-pair catalogue |
| `ids` | Search table below | Regular or explicit storage/paired gate arrays for CZ |
| `astar` | Search table below | Same geometry requirements |

Omitted storage_zone_id resolves from Device storage roles (legacy fallback:
`storage`). For IDS/A* it is a string or string array; multipair accepts one
string. This is not an option to set storage_zone_id to null.

| Search field | IDS default | A* default | Meaning |
| --- | --- | --- | --- |
| `method` | `ids` | `astar` | Must agree with kind |
| `deepening_factor` | 0.01 | 0.8 | Assignment-deviation weight |
| `deepening_value` | 0 | 0.2 | Deviation offset |
| `lookahead_factor` | 0.4 | 0.2 | Next-partner distance weight |
| `reuse_level` | 5 | 5 | Stay-option partner-cost preference |
| `trials` | 4 | 4 | Complete goals compared by IDS |
| `queue_capacity` | 100 | 100 | IDS queue budget |
| `use_window` | true | true | Candidate windows |
| `window_min_width` | 16 | 16 | Initial window column width, nonnegative |
| `window_ratio` | 1.0 | 1.0 | Positive window height/width ratio |
| `window_share` | 0.8 | 0.8 | Nonnegative target count relative to jobs |
| `max_nodes` | 10000000 | 10000000 | IDS visited-node / A* open-set limit per search |
| `storage_zone_id` | Device-derived | Device-derived | Storage selection |
| `geometry` | null | null | Infer arrays or supply the structure below |

A* accepts trials and queue_capacity in the common options object, but its
search call does not use them. Search weights apply to proxy scores, not a
configurable fidelity or total-duration objective. The same search_nodes number
does not imply equal work across IDS and A*.

Explicit `placement.geometry` has required `storage_arrays` and
`entanglement_arrays`. Each entanglement item is `[left_array,right_array]`.
Every array has required `id`, `kind` (`storage/left/right`), `sites` (site matrix),
`pitch` (point), and optional `partner_sites` (site matrix, default empty list).
IDs refer to Device sites; this metadata does not create traps.

### Transport

| Kind | All parameters/defaults | Motion/support |
| --- | --- | --- |
| `pair` | None | Flexible/rigid; at most two physical qubits |
| `sequential` | None | Flexible/rigid; sequential atom transport |
| `strict` | None | Flexible/rigid; compatible batches and occupancy closure |
| `staged` | `geometry:null` | Flexible only; inferred or explicit staging arrays |
| `relaxed` | `geometry:null`, `prefer_split:1.0` | Flexible only; compatible merging with staged fallback |

Higher prefer_split retains more strict groups; zero removes the proxy-cost
threshold, not device/occupancy requirements. Motion rules come from the Device.
There is no separate `rigid` transport kind or JSON rigid-drop switch.

Explicit transport.geometry has required `sites`, an object keyed by site ID.
Each value requires all of: `kind` (`storage/left/right`), `zone_id`, `pitch_x`,
`pitch_y`, `min_x`, `max_x`, `min_y`, `max_y`, `pair_separation`.
Optional `coordinate_quantum` is null (default) or a positive number. These are
staging metadata for existing Device sites. max_x/max_y are one lattice pitch
beyond the final column/row, unlike a tight site bounding box. Null quantum uses
continuous geometry; 1 selects the source generator's integer micrometer divisions.

### Emitter and reuse

| Stage/kind | All parameters/defaults | Behavior |
| --- | --- | --- |
| emitter `layer` | None | Emit each operation; each CZ uses its pair's bounding region |
| emitter `whole_layer` | `coverage:null` | One pulse over catalogue coverage, or explicit coverage |
| reuse `none` | None | No reuse analyzer |
| reuse `matching` | None | Current maximum-cardinality gate-to-gate matching |
| reuse `source` | None | Pinned source endpoint-priority/matching decisions |

Explicit coverage is either `{kind:"region", region:<rectangle>}` or
`{kind:"regions", regions:[<rectangle>,...]}`. Coverage, Rydberg radius, and gate
site positions are different inputs. There is no per-code row-group reuse
configuration or automatic CZ-holder selection objective.

### Nonzoned and combination restrictions

Nonzoned has only `kind` and optional positive `max_cz_gates` (default: catalogue
capacity). It does not accept Search Smarter's stage objects, mode, or reuse
switch. It prepares a CZ layer, pulses, and reverses its transfers to fixed homes.

| Combination | Current requirement |
| --- | --- |
| Search Smarter + enola_initial | Unsupported entry provider |
| Nonzoned + initial_storage | Unsupported entry provider |
| Nonzoned motion/CZ | Flexible AOD, SLM-SLM CZ, allow_spectators=true, declared cells |
| Nonzoned measurement | Retained AOD readout |
| Ready scheduler + max_atoms other than 1 + measurements | Flexible AOD, retain_atoms=true, AOD in readout holders |
| source_asap + measurement/reset/conditions | Unsupported; use ready |
| rigid + staged/relaxed | Unsupported |
| QEC cross-layer reuse + pair placer | Unsupported |
| QEC cross-layer reuse + reuse none | Invalid option combination |
| Catalogue-based CZ without SLM-SLM capability | No usable gate catalogue |

Capability admission reports unsupported jobs before construction where possible.
Explicit-entry occupancy and later construction can impose additional constraints;
an admitted configuration still needs semantic validation of the generated result.

Sources: [compiler registrations](../src/nadit_physical_compiler/compilers/builtin.py),
[stage registry](../src/nadit_physical_compiler/registry.py),
[support predicates](../src/nadit_physical_compiler/support.py),
[job loader](../src/nadit_physical_compiler/job.py),
[job schema](../src/nadit_physical_compiler/schemas/job.py).

## Experiment

### Spec fields

| Field | Required/default | Meaning |
| --- | --- | --- |
| `schema` | Required: `nadit.experiment/1` | Version |
| `name` | Required | Experiment name |
| `suite` | Required | Manifest path/name or `{name,sha256}` |
| `architectures` | Required | Family/parameter scan items below |
| `strategies` | Required | Labels mapped to inline configurations or JSON file paths |
| `strategy_overrides` | `{}` | Label -> parameter-path -> replacement |
| `entry` | Omitted | Global entry override |
| `cases` | `{seeds:[0]}` | Measurement cases |
| `cost` | `[{model:"additive",params:{}}]` | Post-compilation evaluations |
| `budget` | `{timeout_s:900}` | Worker timeout and optional search-node budget |
| `validate` | Required: `required` | Semantic validation is mandatory |
| `store` | `failures` | `none`, `failures`, or `all` saved result policy |
| `baseline` | Optional | Comparison selectors: strategy label and architecture parameter paths |

Each architecture item accepts only `family`, `base`, `params`, `sweep`, `zip`,
`exclude`, `aod`. Family is required. Base can be a parameter object or JSON path;
params provides the inline alternative. If both exist, base takes precedence.
A loaded `{family,params}` wrapper supplies its params, but the scan item's family
still selects the generator. Prefer supplying just one of base/params.

- `sweep` defaults to `{}`: parameter path -> nonempty list of values.
- `zip` defaults to `[]`: groups of sweep paths that advance together; grouped
  lists must have equal lengths. Other axes form a Cartesian product.
- `exclude` defaults to `[]`: dictionaries of path/value equalities; matching
  combinations are removed.
- `aod` defaults to generated params.aod.id, or `transport`.
- Paths support objects and arrays, e.g. `storage[0].rows`. Parent objects must
  already exist; assigning a leaf is allowed.

A strategy value can be a raw strategy object or
`{strategy:<strategy>,entry:<entry>}`, or a file containing either form. Labels
are experiment names, not compiler IDs. Effective entry precedence is:
**global experiment entry > per-strategy entry > compiler default**.

strategy_overrides paths are relative to the strategy, e.g.
`{"qec":{"placement.trials":8}}`. Overrides apply before neutral budgets.

cases has `seeds` (nonempty integer list, default `[0]`) and `overrides` (one
measurement dictionary or a list of dictionaries, default `[{}]`). Seeds and
override cases form a product. Cost items have `model` (default `additive`) and
`params` (object or JSON path, default `{}`). Cost is post-processing and does
not become a placement objective or enter compile run identity.

### Suite and budgets

A suite reference is a path/name string or `{name:<path-or-name>,sha256:<hash>}`.
The optional hash pins the manifest. Lookup also supports bundled `suites/<name>`.
The manifest has schema `nadit.suite/1`, required `name`, `gate_set`, `circuits`,
and optional `source` metadata. Each circuit requires `id`, `file`, SHA-256
`sha256`, and physical `qubits`. Input bytes and declared width are verified.

| Budget | Default | Application |
| --- | --- | --- |
| `timeout_s` | 900 | Positive worker wall-clock timeout; cost post-processing is outside this worker |
| `search_nodes` | Omitted | Positive integer mapped to Search Smarter IDS/A* placement.max_nodes |

Other compilers/placements record search_nodes as `not_applicable`. This field
does not set ENOLA SA iterations. Specify those through the selected entry.
IDS may return a goal already found when its limit is reached; without a goal,
the search reports search_exhausted. A* uses its open-set limit.

### Outputs and run control

Experiments snapshot inputs and code identity, use content-derived run IDs,
write one completed run record to `runs.jsonl`, and resume by reusing recorded
run IDs, including failed runs (resume is not automatic failure retry).
Status values are `ok`, `unsupported`, `invalid_job`, `compile_error`,
`search_exhausted`, `timeout`, and `validation_failed`. Failed cells remain in
the study rather than silently disappearing from a comparison.

| CLI option | Default | Meaning |
| --- | --- | --- |
| run `--output` | artifacts/experiments/<name> | Result directory |
| run `--workers` | 1 | Concurrent workers |
| plan/run `--allow-dirty` | false | Permit a dirty initial snapshot; code still cannot drift during the run |
| run `--new-version` | false | Use a separate versions/<version-hash> subdirectory |
| summarize `--cost-id` | Omitted | Choose a cost evaluation for comparison |
| summarize `--viewers` | false | Export saved anomaly results through the packaged viewer |
| summarize `--objectives` | duration_us quality_loss slm_sites | Pareto dimensions |

Available additional Pareto dimensions are `transfers` and `compile_wall_s`.
`log_fidelity` is reported as a statistic but is not an accepted Pareto objective.
Pareto coordinates are minimized; `quality_loss` is negative log-fidelity.
Summaries produce paired baseline comparisons, Pareto results, anomalies and
viewer commands. A missing/failed baseline is not a successful comparison.

Loss/channel parameters belong to the cost model, not architecture, Device
timing or compiler scoring. See [noise ADR](../../../docs/decisions/0034-modular-noise-estimates.md)
and `nadit describe` for model-specific options. The model choices include
`additive/v1`, `noise/v1`, and optional pinned `hsim-aqc`.

Sources: [experiment spec](../src/nadit_physical_compiler/experiments/spec.py),
[suite](../src/nadit_physical_compiler/experiments/suite.py),
[budget mapping](../src/nadit_physical_compiler/compilers/budgets.py),
[experiment CLI](../src/nadit_physical_compiler/experiments/cli.py).

## Complete examples

The following complete documents use paths relative to this `docs/` directory.
Save them here as `reference-zoned.job.json`, `reference-nonzoned.job.json`, and
`reference.experiment.json`, respectively, or adjust paths for another directory.
The examples use repository circuits; external callers substitute their own
QASM paths and explicit qubit bindings. Numeric values are example choices,
not hardware-calibrated recommendations.

### Zoned job with every applicable IDS option

This job describes four atoms and two gate pairs. The generator parameters are
explicit, including optional naming/order fields. Explicit workspace overrides
margin expansion; explicit side IDs override prefix naming. The job gate_zones
repeats the generated pair catalogue and may be omitted. geometry=null requests
inference; the explicit alternative is described above. Strict has no additional
parameters: relaxed-only fields must not be added to it.

```json
{
  "schema": "nadit.job/1",
  "circuit": {
    "qasm": "../examples/multipair_feedback.qasm3",
    "gate_set": ["rx", "ry", "rz", "cz"]
  },
  "device": {
    "architecture": {
      "family": "zoned/v1",
      "params": {
        "name": "reference-zoned",
        "storage": [{
          "id": "storage", "prefix": "s",
          "origin": {"x": 0, "y": 0}, "rows": 2, "columns": 2,
          "pitch": {"x": 5, "y": 5},
          "bounds": {"x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10},
          "operations": ["load", "store", "rotate", "reset"]
        }],
        "gate_zones": [{
          "id": "entanglement", "array_id": "gate_array",
          "position": {"x": 0, "y": 30}, "rows": 1, "pairs_per_row": 2,
          "separation": 2, "pitch": {"x": 8, "y": 6},
          "bounds": {"x_min": 0, "y_min": 0, "x_max": 12, "y_max": 6},
          "operations": ["load", "store", "cz", "rotate", "reset"],
          "left_prefix": "gate_left", "right_prefix": "gate_right",
          "left_ids": [["gL0", "gL1"]], "right_ids": [["gR0", "gR1"]]
        }],
        "measurement": {
          "id": "measurement", "position": {"x": 0, "y": 60},
          "bounds": {"x_min": 0, "y_min": 0, "x_max": 20, "y_max": 10},
          "operations": ["measure"],
          "readout": {
            "channel": "camera", "duration_us": 10, "latency_us": 500,
            "min_spacing_um": 5, "max_atoms": 4,
            "retain_atoms": true, "holders": ["slm", "aod"]
          }
        },
        "aod": {
          "id": "transport", "motion": "flexible", "min_spacing": 1,
          "max_rows": null, "max_columns": null, "margin": 10,
          "workspace": {"x_min": -10, "y_min": -10, "x_max": 30, "y_max": 80}
        },
        "timing": {
          "load_us": 10, "store_us": 10,
          "move_us_per_um": 1, "move_profile": "linear"
        },
        "cz": {
          "channel": "rydberg", "radius_um": 4.6, "duration_us": 0.5,
          "holder_pairs": [["slm", "slm"], ["slm", "aod"], ["aod", "aod"]],
          "allow_spectators": false
        },
        "local": {
          "channel": "raman", "rotation_us": 1, "reset_us": 5,
          "holders": ["slm", "aod"]
        },
        "global_control": {
          "channel": "global", "rotation_us": 1,
          "holders": ["slm", "aod"], "region": null
        },
        "shared_zones": [],
        "site_order": [
          "s_r0_c0", "s_r0_c1", "s_r1_c0", "s_r1_c1",
          "gL0", "gL1", "gR0", "gR1"
        ]
      }
    }
  },
  "aod": "transport",
  "gate_zones": [{"zone": "entanglement", "pairs": [["gL0", "gR0"], ["gL1", "gR1"]]}],
  "strategy": {
    "kind": "search_smarter", "mode": "adaptive", "cross_layer_reuse": false,
    "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
    "placement": {
      "kind": "ids", "method": "ids", "storage_zone_id": "storage", "geometry": null,
      "deepening_factor": 0.01, "deepening_value": 0, "lookahead_factor": 0.4,
      "reuse_level": 5, "trials": 4, "queue_capacity": 100,
      "use_window": true, "window_min_width": 16, "window_ratio": 1.0,
      "window_share": 0.8, "max_nodes": 10000000
    },
    "transport": {"kind": "strict"},
    "emitter": {"kind": "whole_layer", "coverage": null},
    "reuse": {"kind": "matching"}
  },
  "entry": {"kind": "explicit", "atoms": [
    [0, "s_r0_c0", 0], [1, "s_r0_c1", 1],
    [2, "s_r1_c0", 2], [3, "s_r1_c1", 3]
  ]},
  "case": {"seed": 0, "overrides": {}}
}
```

To select exact per-layer QEC return, change mode to qec and leave the switch
false. To select fixed-home cross-layer reuse, set both mode=qec and the switch
true; retain matching/source. To select A*, change kind and method together and
use the desired A* parameters. To select rigid, set motion=rigid and set
readout.max_atoms=1 for this feedback example; keep strict/pair/sequential.

### Shared-array job with explicit ENOLA entry budget

The referenced [expanded Device](../examples/devices/nonzoned.json) is the output
reproduced by [shared/v1 parameters](../examples/architectures/nonzoned.json).
This example demonstrates the direct Device-path form of job.device.

```json
{
  "schema": "nadit.job/1",
  "circuit": {
    "qasm": "../examples/circuits/nonzoned.qasm3",
    "gate_set": ["rx", "ry", "rz", "cz"]
  },
  "device": "../examples/devices/nonzoned.json",
  "aod": "transport",
  "strategy": {"kind": "nonzoned", "max_cz_gates": 8},
  "entry": {
    "kind": "enola_initial", "array": "cells", "seed": 0,
    "iterations": 100, "moves_per_iteration": 400
  },
  "case": {"seed": 17, "overrides": {}}
}
```

To bypass entry optimization, replace entry with kind=explicit and the atom/site/
qubit triples from [the fixed-entry job](../examples/jobs/nonzoned.json).
Do not add Search Smarter stage options to this strategy.

### Experiment with separate policy entries

This complete spec references the pinned bundled feedback suite and a versioned
zoned generator input. It demonstrates a geometry scan, strategy-specific entries,
neutral budgets and a baseline. It has four cells: two storage pitches by two policies.

```json
{
  "schema": "nadit.experiment/1",
  "name": "configuration-reference",
  "suite": "../suites/feedback/suite.json",
  "architectures": [{
    "family": "zoned/v1",
    "base": "../examples/architectures/multipair_flexible.json",
    "aod": "transport",
    "sweep": {"storage[0].pitch.x": [5, 6]},
    "zip": [], "exclude": []
  }],
  "strategies": {
    "adaptive": {
      "strategy": {
        "kind": "search_smarter", "mode": "adaptive",
        "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
        "placement": {"kind": "ids"}, "transport": {"kind": "strict"},
        "emitter": {"kind": "whole_layer"}, "reuse": {"kind": "matching"}
      },
      "entry": {"kind": "initial_storage", "atom_ids": null}
    },
    "qec": {
      "strategy": {
        "kind": "search_smarter", "mode": "qec", "cross_layer_reuse": false,
        "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
        "placement": {"kind": "ids"}, "transport": {"kind": "strict"},
        "emitter": {"kind": "whole_layer"}, "reuse": {"kind": "none"}
      },
      "entry": {"kind": "initial_storage", "atom_ids": null}
    }
  },
  "strategy_overrides": {"adaptive": {"placement.trials": 4}, "qec": {"placement.trials": 4}},
  "cases": {"seeds": [17], "overrides": [{}]},
  "budget": {"timeout_s": 900, "search_nodes": 1000000},
  "cost": [{"model": "additive/v1", "params": {}}],
  "validate": "required", "store": "all",
  "baseline": {"strategy": "adaptive"}
}
```

Both storage pitches fit the supplied bounds. Global entry is intentionally
omitted so each policy's entry is used. For a mixed-family study,
see [compiler-comparison.json](../examples/experiments/compiler-comparison.json);
unsupported compiler/family cross-product cells are recorded.

## Caller workflow

From `modules/physical_compiler`, inspect the live interface before constructing
configurations. stdout is JSON; use a file or JSON parser rather than scraping
human summaries. These discovery commands do not compile circuits:

```text
uv run --locked nadit describe
uv run --locked nadit describe --json-schema job
```

For the complete jobs above, saved in docs/:

```text
uv run --locked nadit compile docs/reference-zoned.job.json --validate -o artifacts/reference-result.json --text artifacts/reference.atom_ir.txt
uv run --locked nadit validate artifacts/reference-result.json
uv run --locked nadit view artifacts/reference-result.json -o artifacts/reference-viewer.html
uv run --locked nadit compile docs/reference-nonzoned.job.json --validate -o artifacts/reference-nonzoned-result.json
```

Compilation prints the result even with -o. Results contain the native DAG,
Device snapshot, entry/exit state, Atom IR, operation/measurement records, metrics,
input hashes and provenance (including compiler kind/version). A resolved job
records defaults and selected entry. Read output through JSON, not the Atom IR
display text. See [jobs and results](jobs-and-results.md) for result inspection.

| CLI exit | Meaning |
| --- | --- |
| 0 | Command succeeded; inspect per-run statuses for an experiment |
| 1 | Invalid input or path/output error |
| 2 | Compilation failure, including search exhaustion |
| 3 | Validation failure |
| 4 | Unsupported configuration; structured reasons on stdout |

Search exhaustion prints status=search_exhausted and budget_hit=true. Ordinary
input/compiler diagnostics may use stderr; do not assume all failures have the
same JSON shape. Job Schema describes syntax, not all capability or occupancy
constraints. Use normal loading/admission and final result validation as well.

Plan the example experiment before running; keep code fixed during execution:

```text
uv run --locked nadit experiment plan docs/reference.experiment.json
uv run --locked nadit experiment run docs/reference.experiment.json --output artifacts/reference-study --workers 1
uv run --locked nadit experiment status artifacts/reference-study
uv run --locked nadit experiment summarize artifacts/reference-study --objectives duration_us transfers compile_wall_s
uv run --locked nadit experiment summarize artifacts/reference-study --viewers
```

Repeating the run command resumes the same locked study. Reevaluate saved
results with `nadit cost` or `nadit experiment cost` when only noise parameters
change; do not recompile solely to change cost parameters. Save successful
results (`store=all`) if later revaluation/viewing is needed.

Python callers use the same loader and controller; paths passed to loads_job
are resolved against its explicit base_dir. No alternate per-project parsing
or DAG-progress implementation is needed:

```python
from nadit_physical_compiler.job import load_job, compile_job
from nadit_physical_compiler.validation import validate_result

job = load_job("docs/reference-zoned.job.json")
result = compile_job(job)
report = validate_result(
    result, entry_state=job.entry_state, device=job.device, dag=job.dag
)
if not report["ok"]:
    raise ValueError(report)
```

For AI clients: select a registered compiler, use only its declared entry and
options, keep physical units explicit, distinguish geometry from search budgets,
and retain the resolved configuration alongside results. Do not translate a
Device capability into an unimplemented strategy option. The complete examples
are configuration documents, not new compiler algorithms.
