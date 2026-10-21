# 0025: Parametric architectures and strategy support

Status: Implemented on 2026-10-21.

## Context

A Device is a JSON file with explicit zones, SLM sites or grids, AODs, timing,
and capabilities. Gate pairs are not part of the Device: each job lists
`gate_zones[*].pairs` by SLM site ID. Changing the architecture therefore
means editing both the Device file and every job that uses it.

Strategies also assume a particular architecture family without saying so.
Placement and staged transport expect storage arrays and paired left/right
gate arrays (`SLMArray.kind ∈ {storage, left, right}`). Several stages default
to a zone named `"storage"`. A job that combines a strategy with an
architecture it does not handle fails deep in compilation, or produces a
result that only ADR 0023 validation would reject.

Experiments (ADR 0027) need to vary the architecture as a set of parameters,
generate many valid devices from those parameters, and record which
combinations of strategy and architecture are unsupported instead of failing.

## Decision

### Interaction sites in the Device

Device JSON gains optional `pair_arrays`:

```json
"pair_arrays": [
  {"id": "gate_top", "zone": "entanglement",
   "left":  {"prefix": "gt_l", "origin": {"x": 0, "y": 120}, "rows": 1, "columns": 30, "pitch": {"x": 8, "y": 10}},
   "right": {"prefix": "gt_r", "origin": {"x": 2, "y": 120}, "rows": 1, "columns": 30, "pitch": {"x": 8, "y": 10}}}
]
```

Each entry generates two SLM grids, with sites paired by row and column. The
loader validates that:

- each pair's internal distance is at most `cz.radius_um`;
- the nearest site of any other pair is more than `cz.radius_um` away.

An expanded pair array stores `left` and `right` as rectangular matrices of
existing site IDs. This reference form preserves site identity and order in
result snapshots and existing configurations without generating sites twice.
The loader accepts either grid descriptors or these matrices. It checks
shape, distinct sites, zone membership, and interaction distances once at load.

When `job.gate_zones` is omitted, the catalogue is built from all
`pair_arrays`. An explicit `gate_zones` still overrides it, so existing jobs
are unchanged.

Zones gain an optional `role` from `storage`, `entanglement`, and
`measurement`. Stages that default to the zone ID `"storage"` use the zone
with role `storage` when the role is present. An explicit
`storage_zone_id` still wins.

### Architecture families

Add `nadit_physical_compiler.architectures`. A family is a versioned generator
from validated parameters to an expanded Device document:

```python
generate(family: str, params: Mapping[str, Any]) -> dict[str, Any]
```

The first family is `zoned/v1`, covering the existing zoned layout:

| Group | Parameters |
| --- | --- |
| storage | blocks with `rows`, `columns`, `pitch`, `origin` |
| gate zones | list of `position` (offset from storage), `rows`, `pairs_per_row`, `separation`, `pitch` |
| measurement | `position`, `bounds`, readout capability fields |
| aod | `motion`, `max_rows`, `max_columns`, `min_spacing`, `workspace` (default: bounding box plus margin) |
| capabilities | `cz` (radius, duration, holder pairs), `local`, `global_control` |
| timing | preset name (`paper`, `linear`) or explicit Timing fields |

Generation is deterministic. The device `name` is the family plus a short hash
of the canonical parameters. Before returning, the generator checks:

- zones do not overlap unless declared shared;
- the AOD workspace covers every site;
- every pair array satisfies the interaction-distance rules above;
- the storage capacity is reported, so a job can reject circuits with more
  qubits than storage sites.

The existing `configs/*.json` files are expressible as `zoned/v1` parameters.
A test shows that the generated Device equals each existing configuration.

`zoned/v1` uses storage-block origins as absolute coordinates. Gate and
measurement `position` values are offsets from the first storage origin;
their `bounds`, and storage bounds, are local to that origin/position.
The first storage block defines the reference origin only, not a capacity
limit. Optional `name`, gate `left_ids`/`right_ids`, and `site_order` retain
legacy names and ordering for exact reproduction. Without `name`, use the
family and canonical parameter hash. Existing configurations gain role and
pair metadata without changing their sites, ordering, capabilities, or timing.

The implementation lives in `architectures/zoned.py` with geometry helpers
and schemas in separate modules. Parameter fixtures under
`examples/architectures/` reproduce all five existing configurations.
Timing presets are linear (10 us transfers, 1 us/um) and paper (10 us
transfers and the existing paper distance function); explicit Timing fields
remain available. Named shared-zone pairs permit overlapping bounds.

A job's `device` field accepts a third form:

```json
{"architecture": {"family": "zoned/v1", "params": {...}}}
```

The resolved job keeps the parameters, and the result keeps the expanded
Device (ADR 0023).

### Strategy support declaration

Each `Registration` in the registry gains:

```python
supports(context: StrategyContext) -> Support   # Support(ok: bool, reason: str)
```

Initial predicates:

- `ids`, `astar`, `staged`, `source_asap`: storage arrays and left/right pair
  arrays can be inferred, or explicit geometry is given;
- `staged`, `relaxed`: the AOD is flexible (ADR 0024);
- `pair` (placement and transport): at most two qubits;
- `whole_layer`: a catalogue or explicit coverage exists.

`source_asap` also declares its existing static-circuit restriction; feedback
uses `ready`. Initial-storage admission reports unavailable geometry or
insufficient storage capacity as unsupported before compilation.

`build_strategy` evaluates every stage's predicate before constructing any
object. If one fails, `load_job` raises `UnsupportedJob` with every failed
stage and its reason. `nadit compile` exits with code 4 and writes
`{"status": "unsupported", "reasons": [...]}` to stdout. Exit codes 0 to 3
keep their ADR 0021 meanings.

### Discovery

`nadit describe` prints one JSON document containing:

- every architecture family, with its parameter schema, defaults, and
  constraints;
- every registered stage kind, with its options, defaults, and a description
  of its support predicate;
- the job and result schema versions.

`nadit describe --json-schema job` prints a JSON Schema for `nadit.job/1`.
Agents use these instead of reading prose documentation.

## Alternatives

Keeping gate pairs in jobs keeps devices smaller, but makes every
architecture change a multi-file edit and lets jobs disagree with their
Device. A free-form Python architecture hook would be more flexible, but its
parameters could not be swept, hashed, or described to an agent. Detecting
unsupported combinations by catching compiler exceptions would mix real
compiler failures with expected incompatibility.

## Consequences

Architecture becomes data that experiments can sweep. Existing Device files
and jobs keep working. Families beyond `zoned/v1` (non-zoned, multiple AODs,
AOD-held gates) are added as new families, together with the strategies that
support them. Until then, `describe` and the support predicates make the
current limits explicit.

Support checks are job admission checks. Syntax and parameter errors retain
exit code 1; unsupported stage/device combinations return all failed stages
with exit code 4, before constructing strategy objects. Existing direct
transport calls retain their construct-time rejection. Storage roles resolve
omitted options; explicit zone IDs win. Array-based placement may select
multiple storage-role zones; fixed multipair placement requires one.
IDS/A* require arrays when placing CZ gates. Their existing local-only path
with explicit entry remains lazy and does not require unused CZ geometry.

`support.py` owns Support/UnsupportedJob and reusable predicates. The registry
binds a predicate and its description to each registration. Discovery and the
job schema are generated from the same registrations and architecture schema.
The standard JSON Schema validator is used only for verification in a temporary
tool environment. No project dependency was added. Normal compilation keeps
the existing small input readers and does not run a full schema checker.

## Implementation order

1. Zone `role` and `pair_arrays` loading with interaction-distance checks.
   Accept: an existing catalogue built from `pair_arrays` equals the explicit
   `gate_zones` catalogue, and every existing job result is unchanged.
2. `zoned/v1` generator and its constraints. Accept: the current configs are
   reproduced exactly; invalid parameters report their parameter path.
3. Architecture form of `job.device`. Accept: generated and file-based
   devices compile identically.
4. Support predicates, `UnsupportedJob`, and exit code 4. Accept: rigid plus
   `staged` and three qubits plus `pair` are each rejected with reasons, and
   all supported examples are unaffected.
5. `nadit describe` and the job JSON Schema. Accept: the schema validates
   every example job and rejects documents with unknown keys.

## Verification

- Baseline: 1,516 tests. Completed modules: 1,575 tests passed; Ruff formatting,
  lint, and mypy passed. Tests ran as one suite after the modules were written.
- All five parameter fixtures reproduce their corresponding Device exactly,
  including names, site IDs/order, roles/pairs, geometry, and capabilities.
- File-based and generated jobs produce identical compilation results for
  both measurement outcomes on every profile. Tests independently check
  semantic validity and final replay, including multiple storage/gate regions.
- JSON Schema Draft 2020-12, checked with jsonschema 4.26.0 in a temporary
  environment: 3 raw jobs, 3 resolved jobs, 3 inline-device jobs, 5 parameter
  files, and 5 device files accepted; 30 unknown-key mutations rejected.
- The generated example compiles through the CLI and its saved result passes
  `nadit validate`. Rigid plus staged emits an unsupported reason and exit 4.

The old four-qubit `pair` construction case now checks rejection; rigid
staged/relaxed CLI assertions now check exit 4 and the exact JSON reason.
The semantic Device-override test removes the obsolete pair declaration when
reducing the CZ radius, preserving its exit-3 and `missing_interaction`
assertions. Pair declarations that remain geometrically inconsistent are
input errors at Device loading.
