# Physical Compiler

## Parametric architecture and strategy discovery

Device zones may declare `role: storage | entanglement | measurement`.
Omitted storage options use those roles; explicit IDs still win. Device
`pair_arrays` declare left/right grids paired by row and column, or matrices
of existing site IDs. Their loader checks zone membership and interaction
distances once. Omitting job `gate_zones` selects all Device pair arrays;
an explicit catalogue, including an empty one, overrides them.

Job `device` accepts a path, an inline Device, or
`{"architecture": {"family": "zoned/v1", "params": {...}}}`. The generator
expands storage blocks, gate arrays, measurement regions, one AOD, and timing.
`examples/architectures/` contains parameter files that exactly reproduce
all five current configs, including site identity/order and capabilities.
Storage capacity is available as `Device.storage_capacity`; initial placement
reports insufficient capacity before compiling gates. Parameters remain in
the resolved job; results embed the expanded Device.

```text
uv run --locked nadit describe
uv run --locked nadit describe --json-schema job
uv run --locked nadit compile examples/jobs/generated_zoned.json --validate -o RESULT.json
```

Each registry stage declares `supports(context) -> Support`. Admission
collects unsupported stages before constructing their objects. CLI exit code
4 prints `{"status": "unsupported", "reasons": [...]}` and creates no result
or Atom IR output. Invalid input remains exit 1, compiler failure exit 2,
and semantic-validation failure exit 3. `source_asap` requires a static
circuit; `ready` retains measurement feedback. See
[ADR 0025](../../docs/decisions/0025-parametric-architectures-and-strategy-support.md).

## Result documents

The CLI writes self-contained `nadit.result/2` JSON: Atom IR, layouts,
compilation records, the native DAG, an expanded Device snapshot, and input
content hashes. `decode_result(document)`, `decode_dag(document["dag"])`,
and `decode_layout(document["entry_state"])` restore the saved records.
In Python, pass `dag`, `device`, and `inputs` to `encode_result` for version 2;
omitting all three retains version-1 serialization.
`metrics.collect_metrics(result)` computes the shared counts and durations;
`encode_result` includes them automatically. Transport duration is the sum of
Load/Move/Store action durations; program duration follows parallel steps,
and result duration also includes pending measurement feedback latency.
`validation.validate_result(result, entry_state=entry, device=device, dag=dag)`
checks transport, source coverage, replay, pulse interactions, addressing,
dependencies, and measurement feedback. The report gives the first failure,
up to 20 failure details, the total count, and the check groups that ran.
Actions use their common step-entry layout. Validation runs only when requested;
the existing call without a DAG retains its compact structural report.
`AtomProgram.to_text()` remains the human-readable display.
See [ADR 0021](../../docs/decisions/0021-job-spec-and-result-document.md)
for the job, metrics, and CLI interfaces, and
[ADR 0023](../../docs/decisions/0023-semantic-validation-and-self-contained-results.md)
for self-contained results and semantic checks.

`job.load_job(path)` resolves a `nadit.job/1` document relative to its own
directory, constructs its configured modules through `registry.py`, and
returns a `CompilationJob`. `job.compile_job(job)` runs the existing compiler.
The job retains the resolved configuration, Device, DAG, entry layout,
strategy, and input hashes. Measurement overrides refer to source measurement
IDs. Job identity uses input content hashes in place of file paths; provenance
also records whether tracked files differ from the recorded git revision.

Run saved jobs and inspect their results from the module directory:

```text
uv run --locked nadit compile examples/jobs/multipair.json -o RESULT.json --validate --text PROGRAM.txt
uv run --locked nadit validate RESULT.json
uv run --locked nadit validate RESULT.json --device configs/multipair_flexible.json
uv run --locked nadit compare A.json B.json
```

Commands write JSON to stdout and diagnostics to stderr. `compile -o` also
saves the same result document; `--text` writes the display-only Atom IR.
Exit codes are 0 for success, 1 for invalid input, 2 for compilation failure,
and 3 for a failed validation report. Version-2 validation reads the embedded
DAG and Device without accessing either input file. `--device` explicitly
replaces the Device and records its path in the report. Version-1 results
retain structural validation and report `semantic: "unavailable"` in the CLI.
See [Jobs and saved results](docs/jobs-and-results.md) for all stage options,
entry bindings, result fields, metrics, and the saved-result viewer.

## Current status

Implemented: the OpenQASM 2.0 / 3.0 frontend, Physical DAG with measurement
conditions, seeded `FakeBackend`, and `execute` as a feedback reference tester.
The flexible device foundation provides geometry, atom holders, active AOD
lines, and loading, movement, and unloading constraint checks.
`physical_ir.py` defines circuit operations and dependencies. `atom_ir.py`
provides atom actions, parallel steps, program timing, and text output.
The frontend uses Qiskit for parsing, custom-gate expansion, native translation,
and wire dependencies. Native translation finishes before PhysicalDAG
construction. Small records and protocols define module handoffs.
The tester produces an abstract physical-circuit trace.
The compiler connects ready-layer selection, fixed-pair placement, transport,
and Atom IR emission, including measurement feedback. The multi-pair baseline
adds a fixed catalogue, capacity-limited CZ layers, sequential transfers, and
one shared pulse per CZ layer. Direct replay reconstructs the exit layout.
Strict parallel transport uses shared geometry rules, a conflict map, and
current occupancy to construct common-load movement batches.
Optional matching reuse retains compatible atoms at gate sites across known
CZ layers. Lookahead stops at unresolved feedback without sampling.
Routing-aware placement adds source-aligned candidate generation, compatible
movement-group scoring, optional reuse, and bounded Iterative Diving Search.
Its layout path includes automatic storage initialization, nearest-SLM/site
queries, and the source candidate windows and expansion.
The static source profile adds the original capacity-based ASAP layers,
endpoint-priority reuse, A* alongside IDS, integer-unit staging offsets,
multi-region CZ, LocalU, GlobalRY, and the paper's distance-to-time function.
The complete original C++ stages can be compared on the same native inputs;
see [the pipeline reference](reference/pipeline_README.md).

## Source pipeline and application profiles

Staged strict routing provides row/column pickup,
offsets, and release; see
[ADR 0015](../../docs/decisions/0015-staged-strict-transport.md).
Relaxed routing adds compatible batch merging and staged reordering; see
[ADR 0016](../../docs/decisions/0016-relaxed-routing.md).
Rigid motion uses the same interfaces; see the comparison below.
The [device design](docs/device-and-zones.md) records the accepted zoning,
interaction-position, and AOD measurement requirements. The
[implementation plan](docs/implementation-plan.md) gives the file paths and
acceptance criteria through the flexible pipeline and rigid-device comparison.

The [full-pipeline decision](../../docs/decisions/0019-full-source-pipeline.md)
records the implementation and evidence boundaries. The
[benchmark runner](reference/benchmark_README.md) prepares the paper's 26
circuits and compares A* strict, IDS strict, and IDS relaxed using saved
native inputs. Its execution budget and first differing stage are recorded
separately from correctness tests.

`SourceASAPScheduler(dag, geometry, max_filling_factor=0.9)` precomputes the
source's static single-qubit and CZ phases and implements the same read-only
`select` protocol as `ReadyScheduler`. It does not own DAG progress.
`SourceReuseAnalyzer` preserves the source's first-endpoint matching graph;
`MatchingReuseAnalyzer` remains the complete-shared-edge application policy.
The online scheduler remains available for measurement feedback.

`PlacementConfig.for_method("astar")` selects the source class defaults and
`max_nodes` bounds its open set. The benchmark runner applies the paper's
explicit A* overrides. IDS and A* share candidate generation and score functions.

`GatePairCatalog.from_zones(device, ((zone_id, pair_ids), ...))` joins multiple
gate arrays. Its `RegionSet` coverage illuminates their union, excluding gaps.
Placement, spectator clearing, and `WholeLayerEmitter` share that coverage.
`PlacementGeometry.from_device` builds one paired array per region.

`GlobalControl(channel, rotation_us, region=...)` configures a global Y pulse.
An omitted region uses the bounding region of Device zones; the source profile
explicitly selects the storage-only bounds used by original code generation.
`LocalU` retains the OpenQASM U(theta, phi, lambda) convention. The frontend can
retain U gates with `GateSet(("u", "rz", "cz"))`; existing RX/RY/RZ defaults remain.

`Timing(..., move_profile="paper")` applies the original piecewise distance
function; `"linear"` remains the default. Explicit
`StagingGeometry(..., coordinate_quantum=1.0)` reproduces the original integer
micrometre division for intermediate offsets. Its default `None` retains
continuous coordinates. Neither setting introduces a dynamics simulation.

The accepted [compilation design](../../docs/decisions/0005-case-driven-physical-compilation.md)
places DAG progress, the ready set, predicted atom positions and holders, and
planned time inside the physical compiler. Device capabilities determine
whether a candidate batch is legal.

When compiling a measurement, the compiler obtains and stores one fake bit
using the seed and stable source measurement ID. Successor conditions then
select emitted operations or skips and release dependencies. Compilation
decisions advance the DAG directly. Planned times still enforce measurement
completion plus result latency before dependent operations.

## Rigid AOD comparison

`AOD.motion` selects `"flexible"` (default) or `"rigid"`. Rigid MOVE translates
all active rows and columns by one vector, including empty lines. A rigid AOD
carrying atoms cannot activate additional lines during LOAD. Transfer and
distance-based timing are shared with flexible motion.

`rules_for(aod, timing)` supplies `TransportRules` to placement scoring and
transport. `RigidRules` groups requests only when their displacement vectors
agree and their source-axis relations permit a common grid. Pair, sequential,
and strict transport support this model. Staged and relaxed reject rigid jobs
at loading; direct Python construction rejects them before any fallback.
Job rejection uses the strategy-support interface and CLI exit code 4.

Three configurations share zones, sites, operation capabilities, and timing:
`configs/zoned_flexible.json` (unlimited), `configs/zoned_flexible_16.json`
(16 by 16), and `configs/zoned_rigid_16.json` (16 by 16). Storage pitch is 5 um;
20 isolated gate pairs occupy one row, with 2 um separation within each pair.

From this module directory, compare identical circuits and measurement cases:

```text
uv run --locked python -m examples.compare_motion --output artifacts/rigid-aod/comparison.json --results-dir artifacts/rigid-aod/results
```

The report includes a 40-qubit capacity case and both outcomes of the existing
feedback circuit. Each row has action counts, atom transfers, transport time,
semantic validation, and final replay equality. Each saved result embeds its
Device and DAG and can be passed to the viewer below. `--placement multipair`
selects fixed placement; `--transport sequential` selects singleton transport.
See [ADR 0024](../../docs/decisions/0024-rigid-aod-motion.md) for the contract
and remaining hardware questions.

## Animated Atom IR replay

View a saved result without recompilation:

```text
uv run --locked python -m examples.visualize_compilation --result RESULT.json --output artifacts/result-viewer/index.html
```

Version-2 results supply their embedded Device, so the input files may be
moved or removed. Version-1 results use `job.device`; `--device DEVICE.json`
provides an explicit replacement for either version.

Generate an offline viewer from three real compilations:

```text
uv run --locked python -m examples.visualize_compilation
```

Open `artifacts/atom-replay/index.html` in a browser. The adjacent `traces.json`
contains the same replay data, input QASM, and compiler configuration. The
examples cover both forced measurement outcomes and a local 60-atom parallel
circuit. The latter is a demonstration circuit, not a paper benchmark input.

The viewer supports playback, step and time seeking, zoom/pan, atom inspection,
AOD lines, SLM sites, pulse coverage, and measurement availability. It reads
compiled actions and uses linear endpoint interpolation to show MOVE. Boundary
states come from the existing replay implementation. See the
[viewer contract and usage](docs/atom-replay-viewer.md).

## Layout

```text
physical_compiler/
├── README.md
├── pyproject.toml
├── uv.lock
├── src/nadit_physical_compiler/
│   ├── __init__.py
│   ├── qasm.py         # Compatibility exports for frontend loaders
│   ├── frontend/
│   │   ├── qasm.py     # Parsing, custom-gate expansion, condition binding
│   │   ├── native.py   # GateSet translation with source provenance
│   │   └── dag.py      # Native circuit to PhysicalDAG
│   ├── contracts.py    # Gate-layer, placement, and Atom IR fragment handoffs
│   ├── compiler.py     # DAG progress, planned time, and case-specific results
│   ├── capabilities.py # CZ, local control, and readout capabilities
│   ├── scheduling/    # Ready-layer selection and pure next-CZ preview
│   ├── reuse/         # Maximum matching of shared CZ endpoints
│   ├── placement/     # Fixed placement, candidates, scoring, and IDS
│   ├── transport/     # Constructed AOD transfers and movement
│   ├── visualization/ # Sparse Atom IR export and an offline browser viewer
│   ├── emission/      # Bind gates to existing Atom IR actions
│   ├── strategies/    # Compose stage implementations
│   ├── replay.py      # Apply atom actions to positions and holders
│   ├── serialization.py # Versioned Atom IR, DAG, Device, and result snapshots
│   ├── provenance.py  # Content identities and software/git provenance
│   ├── validation.py  # Optional structural and semantic result checks
│   ├── semantic_actions.py # Pulse, addressing, and readout checks
│   ├── validation_timing.py # Dependency, branch, and feedback checks
│   ├── physical_ir.py  # Circuit operations, dependencies, result references
│   ├── atom_ir.py      # Atom actions, parallel steps, timing, text output
│   ├── execution.py    # Physical DAG feedback reference tester
│   ├── device.py       # Static geometry, timing, and JSON loading
│   ├── state.py        # Atom holders, active AOD lines, entry-state validation
│   ├── motion.py       # Reference transport checks for tests/debugging
│   └── py.typed        # Package typing marker
├── examples/     # Feedback study and concrete Atom IR example
├── tests/        # Frontend, IR, and execution tests
├── docs/         # Module design notes
└── configs/      # Example device configurations
```

This module belongs to the shared NADiT repository. Empty directories retain
`.gitkeep` files.

Read the root [engineering standards](../../agent.md) and
[architecture](../../architecture.md) before working here.

## Setup and verification

Use Python 3.12 and run these commands from `modules/physical_compiler/`:

```text
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy .
```

Runtime dependencies are Qiskit 2.6.0 and `qiskit-qasm3-import` 0.6.0.
The module-local lockfile also fixes the development environment.

## Public API

```python
from nadit_physical_compiler import loads_qasm

dag = loads_qasm("""
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;
""")

assert {node.name for node in dag.nodes} <= {"rx", "ry", "rz", "cz", "measure"}
assert [node.result.measurement_id for node in dag.nodes if node.result] == [2, 3]
print([(node.id, node.source_id, node.name) for node in dag.nodes])
```

To parse an existing circuit file, pass its path:

```python
from nadit_physical_compiler import load_qasm

dag_from_file = load_qasm("circuit.qasm")
```

- `loads_qasm(source: str, *, gate_set=DEFAULT_GATE_SET) -> PhysicalDAG`
  parses and translates an in-memory circuit.
- `load_qasm(path, *, gate_set=DEFAULT_GATE_SET) -> PhysicalDAG`
  parses and translates a circuit file.

Both entry points return NADiT records; downstream callers do not need Qiskit
objects. `GateSet` selects the target gate names and defaults to
`("rx", "ry", "rz", "cz")`. Measurement, reset, barrier, and phase metadata
keep their semantics. For example, `GateSet(("u", "cz"))` selects another
native basis. The selected basis must express the input gates.

## Module handoffs

The `contracts` module defines immutable records using the existing Physical
DAG, Atom IR, and atom state types:

| Record | Fields and meaning |
| --- | --- |
| `GateLayer` | `operation_ids`: selected native DAG nodes |
| `PlacementPlan` | `execution_layout`: layout for the layer; `exit_layout`: desired layout after the layer; optional `preparation_layout`: intermediate arrangement before arrival |
| `AtomFragment` | `steps`: generated AtomSteps; `exit_layout`: resulting state; `completed_operation_ids`: completed native DAG nodes; `retained_atoms`: selected stays for the next layer |

`Scheduler.select`, `ReuseAnalyzer.analyze`, `Placer.place`, and
`Transporter.construct` are typing protocols for interchangeable helpers.
Each implementation owns its options. Transport returns existing AtomSteps;
emission binds action parameters and durations. These interfaces do not
own compilation state. The initial implementations live in `scheduling/`,
`reuse/`, `placement/`, `transport/`, and `emission/`.

`CompilationStrategy.lookahead_enabled` requests a next-CZ preview;
`lower(..., following=None, retained_atoms=())` receives that optional GateLayer
and the previous fragment's actual retained-atom snapshots. The compiler
owns the preview's progress inputs. Scheduler selection is deterministic
and does not mutate scheduler state.
The strategy forwards `following` to `Placer.place` for partner-distance
estimates. The fixed placers accept the hint without changing their decisions.
The strategy also forwards incoming `retained_atoms`, separately from outgoing
`reuse_qubits` proposals. Retention metadata does not change Atom IR.

## One-pair compilation

`compile_physical(dag, device, *, strategy, entry_state, measurement_backend=None)`
returns a `CompilationResult`. The input DAG must already use RX/RY/RZ/CZ,
measurement, reset, barrier, and phase metadata. The entry state supplies the
physical-qubit-to-atom binding.

```python
from nadit_physical_compiler import AtomLayout, AtomState, SLMHolder, compile_physical
from nadit_physical_compiler import load_device, load_qasm, replay
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import PairTransport

device = load_device("configs/one_pair_flexible.json")
dag = load_qasm("examples/pair_feedback.qasm3")
entry = AtomLayout(
    tuple(
        AtomState(atom, device.site(site).position, SLMHolder(site))
        for atom, site in ((7, "storage_r0_c0"), (11, "storage_r0_c1"))
    ),
    qubit_to_atom=(7, 11),
)
strategy = SearchSmarterStrategy(
    ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
)
result = compile_physical(dag, device, strategy=strategy, entry_state=entry)
assert replay(result.program, entry, device) == result.exit_state
print(result.program.to_text())
```

The compiler owns the ready set. It resolves conditions, selects a layer,
commits the strategy's constructed steps and exit layout, and releases
successors. Each measurement samples its stable result identity once.
Independent ready operations can fill feedback latency; otherwise the compiler
inserts WAIT until the required result is available.

| Result field | Meaning |
| --- | --- |
| `program` | AtomProgram with selected global phase and a step timeline |
| `exit_state` | Final atom positions, holders, and qubit binding |
| `operations` | Native-ID-ordered `emitted`, `skipped`, or `metadata` decisions with action start/end times in us |
| `measurements` | Stable result references, fake bits, and availability times in us |
| `layers` | GateLayer records selected for action construction |
| `duration_us` | Completion time including any final pending readout result |

The initial strategy supports at most two atoms and one flexible AOD. CZ uses
fixed SLM pairs in a common CZ zone. The shortest-distance assignment is
selected, and atoms remain at that gate layout for following operations.
Same-row, order-preserving pair transfers run together; other transfers use
singleton batches. Local rotations on distinct atoms can share a step.

Readout moves one atom into the measurement region while held by the AOD,
measures it there, and returns it to its previous SLM site. This frees the AOD
and supports later operations on either atom. Readout retains atoms. Cyclic
swaps requiring a temporary site, parked AOD control groups, and destructive
readout remain subsequent work. The following strategies add multi-pair
transport and IDS placement.

Run both feedback branches and print their actions:

```text
uv run --locked python examples/compile_pair.py --bit 0
uv run --locked python examples/compile_pair.py --bit 1
```

The example's pulse/readout times are explicit study parameters. Tests check
each emitted transport with the reference constraints, reconstruct the exit
state, and check conditional selection, timing, and branch phase equivalence.

## Multi-pair correctness baseline

Use the same compiler and strategy handoffs with these helpers:

| Helper | Responsibility |
| --- | --- |
| `GatePairCatalog.from_device(device, zone_id, pairs)` | Bind explicit SLM site pairs and check their shared-pulse geometry once |
| `ReadyScheduler(max_cz_gates=catalog.capacity)` | Drain ready local rotations, then select disjoint CZ gates up to the fixed pair capacity |
| `MultiPairPlacer(aod_id, catalog, storage_zone_id="storage")` | Assign distinct pairs, clear spectators, and plan storage returns |
| `SequentialTransport(aod_id)` | Move one atom at a time, following destination vacancy order |
| `WholeLayerEmitter(catalog.coverage)` | Emit one CZPulse carrying every selected CZ node ID |

The catalogue's coverage is the full selected CZ zone. Pair endpoints must
be distinct, each pair must lie within the CZ radius, and atoms belonging
to different catalogue pairs must be farther apart than that radius.
Use the same bound device when constructing and using the catalogue.

```python
from nadit_physical_compiler import load_device
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import SequentialTransport

device = load_device("configs/multipair_flexible.json")
catalog = GatePairCatalog.from_device(
    device,
    "entanglement",
    (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right")),
)
strategy = SearchSmarterStrategy(
    ReadyScheduler(max_cz_gates=catalog.capacity),
    MultiPairPlacer("transport", catalog),
    SequentialTransport("transport"),
    WholeLayerEmitter(catalog.coverage),
)
assert catalog.capacity == 2
```

Placement assigns unused candidate pairs greedily by movement distance, with
deterministic ties. CZ participants return to storage after each layer. Other
atoms inside pulse coverage also receive storage destinations, including
unbound spectators. Existing storage sites are retained where possible.
This supports changing interaction partners without retaining old gate pairs.

Every transport step handles one atom using one AOD row and one column.
The constructor maintains occupancy and releases blocked destinations as
their occupants leave. Closed swaps requiring a buffer and parked AOD groups
remain outside this constructor. Readout retains one atom in AOD and returns
it to its prior SLM site. The compiler's measurement sampling and condition
handling are unchanged.

Run the complete four-qubit example with two CZ pairs per pulse, two layers
using different partners, and a measured conditional layer:

```text
uv run --locked python examples/compile_multipair.py --bit 0
uv run --locked python examples/compile_multipair.py --bit 1
uv run --locked python examples/compile_multipair.py --bit 1 --capacity 1
```

The example prints pulse sizes, transfer counts, timing, result identities,
and the complete Atom IR. Integration tests enumerate actual interactions
from illuminated atom positions, compare them with intended source gates,
and check dependency timing and exit-state replay.

Further transport optimization follows the sequence in
[ADR 0010](../../docs/decisions/0010-multipair-compilation.md).

## Following-layer preview and matching reuse

Configure the existing stage composition with a reuse analyzer:

```python
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.transport import StrictTransport

strategy = SearchSmarterStrategy(
    ReadyScheduler(max_cz_gates=catalog.capacity),
    MultiPairPlacer("transport", catalog),
    StrictTransport("transport"),
    WholeLayerEmitter(catalog.coverage),
    reuse_analyzer=MatchingReuseAnalyzer(),
)
```

Without `reuse_analyzer`, the multi-pair storage-return baseline remains
available. With it, the compiler previews the next selected CZ layer using
a copied ready frontier and sparse dependency-count changes. It can pass
resolved barriers, phase metadata, false conditions, and addressed rotations.
It stops at a selected uncompiled measurement, reset, unsupported operation,
or a frontier condition without a compiled result. Already compiled results
can resolve branches even while their planned feedback latency is pending.
The compiler still delays actual conditional operations until measurement
completion plus feedback latency. Preview does not sample, commit operations,
or change the program phase. The existing FakeBackend is called once per
measurement identity; later reads share the compiler's recorded result.

An independent CZ layer can run before a previewed conditional layer while
feedback is pending. Placement uses the actual selected layer and clears
nonparticipating retained atoms from pulse coverage.

`MatchingReuseAnalyzer.analyze(dag, current, following)` returns physical
qubit IDs. It maximizes matching cardinality between the two layers' gates,
retaining their shared endpoints. Identical unordered pairs retain both
atoms. For `(a,b), (c,d)` followed by `(a,c), (b,d)`, two compatible anchors
remain; the other two atoms return to storage before joining new partners.

The placer reserves pairs containing explicitly retained participants first.
The strategy records outgoing reuse candidates only when their execution and
exit states match. The next placement uses these snapshots together with the
actual layout. A local operation that leaves an anchor elsewhere clears its
retention; moving another atom into a gate site does not create a new anchor.
The existing strict transporter constructs all transfers around stationary
atoms. The final CZ layer returns its participants to storage; individual
storage assignments may differ from the entry layout.

Before CZ placement, any other atoms in pulse coverage receive initially
vacant storage sites. Placement returns this optional preparation layout;
the strategy emits its transport before gate arrival, including its time.
Both greedy and IDS placement share this preparation. It requires sufficient
vacant reachable storage and keeps valid retained participants stationary.
See [ADR 0014](../../docs/decisions/0014-explicit-retained-atoms.md).

```text
uv run --locked python examples/compile_multipair.py --transport strict --bit 0 --reuse
uv run --locked python examples/compile_multipair.py --transport strict --bit 1 --reuse
```

Historical baseline before the source audit in ADR 0017, with the example
device and pair capacity 2:

| Bit | Placement and reuse | Atom loads | Atom stores | LOAD / STORE batches | Transport time (us) |
| --- | --- | ---: | ---: | ---: | ---: |
| 0 | Greedy, no reuse | 17 | 17 | 7 / 7 | 1340.949 |
| 0 | Greedy, fixed matching reuse | 13 | 13 | 8 / 8 | 1472.926 |
| 0 | IDS, optional matching reuse | 13 | 13 | 6 / 6 | 1141.125 |
| 1 | Greedy, no reuse | 25 | 25 | 9 / 9 | 1621.157 |
| 1 | Greedy, fixed matching reuse | 21 | 21 | 12 / 12 | 2013.171 |
| 1 | IDS, optional matching reuse | 21 | 21 | 9 / 9 | 1531.643 |

Atom loads and stores sum the atoms in each transfer, including readout.
Batch counts count LOAD and STORE actions. Retained anchors
reduce this count but can split strict batches and change later storage
assignments. Maximum matching optimizes retained atom count; transport-aware
placement is needed to optimize duration. Both cases preserve source-operation
decisions, stable results, pulse interactions, and exit-state replay.
See [ADR 0012](../../docs/decisions/0012-lookahead-and-matching-reuse.md).

## Routing-aware IDS placement

`HeuristicPlacer` follows MQT QMAP's candidate, compatibility-group, lookahead,
and IDS structure at commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`.
It first searches gate assignments and then storage/stay assignments.
Matching supplies reuse candidates; the exit search decides which actually
remain at gate sites.

```python
from nadit_physical_compiler.placement import HeuristicPlacer, PlacementConfig

placer = HeuristicPlacer(
    "transport",
    catalog,
    config=PlacementConfig(
        trials=4,
        queue_capacity=100,
        deepening_factor=0.01,
        deepening_value=0.0,
        lookahead_factor=0.4,
        reuse_level=5.0,
        use_window=True,
        window_min_width=16,
        window_ratio=1.0,
        window_share=0.8,
    ),
)
```

Use it in the existing `SearchSmarterStrategy` with `StrictTransport` and
`MatchingReuseAnalyzer`. The default example still uses `MultiPairPlacer`.
`--reuse` enables matching and the compiler's next-layer preview.

`PlacementGeometry` records ordered storage and paired gate arrays. Regular
Device sites and the selected catalogue can be inferred with `from_device`;
explicit `SLMArray` records supply array grouping when flat coordinates do not.
Nearest queries select an SLM and then project onto its grid, independently of
occupancy. Results are cached on demand.

The source window is enabled by default. It expands within the nearest SLM
until the requested candidate share is reached or the full array is covered.
`use_window=False` enumerates the full nearest array. Each pair contributes its
source-selected orientation. Gate options retain the original score-distance
ordering separately from actual movement endpoints; storage options include a
stay alternative for matched atoms. Ranks used by the heuristic cover all free
target sites, including sites outside the selected window.

`make_initial_layout(num_qubits, geometry, atom_ids=...)` fills storage from the
side toward the gate zone. `placer.initial_layout(num_qubits, device)` provides
the same initialization through `HeuristicPlacer`. The compiler still accepts
an explicit `entry_state`. The example uses automatic initialization for every
comparison mode.

Low-level `gate_jobs` and `storage_jobs` calls without geometry retain their
generic catalogue behavior. `HeuristicPlacer` uses the source geometry path.

The score is the sum of the square roots of each compatibility group's maximum
distance, plus partner-lookahead costs. Groups are extended in first-fit order
using the shared strict Device rules. The remaining-job heuristic follows the
reference's available-option distance, mean lookahead, and axis-displacement
deviation terms. IDS dives into the best child, keeps bounded alternatives,
and compares a configured number of complete placements.

These scores are proxies, not microseconds. Search does not emit actions or
call the transporter for candidate evaluation. Strict transport constructs the
selected movements with occupancy closure; its actual LOAD/MOVE/STORE durations
provide the reported transport time. The compiler still owns measurements,
case selection, and result-availability timing.

Compare three modes with the same circuit, Device, and forced result:

```text
uv run --locked python examples/compile_multipair.py --transport strict --bit 1
uv run --locked python examples/compile_multipair.py --transport strict --bit 1 --reuse
uv run --locked python examples/compile_multipair.py --transport strict --bit 1 --reuse --placement ids
```

Repeat with `--bit 0` for the other branch. `--trials`, `--queue-capacity`, and
`--reuse-level` configure the IDS example. `--no-window`, `--window-min-width`,
`--window-ratio`, and `--window-share` configure candidate enumeration. All
search settings and initial SLM sites are included in its JSON output.

See [ADR 0013](../../docs/decisions/0013-routing-aware-ids-placement.md) for
the score and interface contract and the
[third-party notice](../../docs/third-party-notices.md) for source attribution.
The source layout path and explicit source-defect corrections are recorded in
[ADR 0018](../../docs/decisions/0018-source-placement-path.md); executable C++
comparisons are described in [the placement reference](reference/placement_README.md).

## Shared rules and strict parallel transport

`StrictTransport(aod_id)` implements the same `construct` interface as
`SequentialTransport`. Use it in the existing strategy composition to batch
SLM-to-SLM movements. Retained singleton AOD readout uses the existing
sequential path.

The shared `transport.rules` API provides:

| API | Meaning |
| --- | --- |
| `MoveRequest(atom_id, source, target)` | Positions involved in one requested atom movement |
| `StrictRules(aod, timing).compatible(a, b)` | Pairwise axis equality, order, and minimum-spacing compatibility |
| `build_conflict_map(moves, rules)` | Atom-ID adjacency for incompatible movement pairs |
| `rules.can_fit(moves)` | Batch workspace, distinct-row/column capacity, and spacing, given pairwise compatibility |
| `rules.frames(moves)` | Source/target grids with stable line IDs and atom bindings |
| `rules.duration_us(moves)` | Load + longest movement + store time for the direct batch |

```python
from nadit_physical_compiler import Position, load_device
from nadit_physical_compiler.transport import (
    MoveRequest,
    StrictRules,
    build_conflict_map,
)

device = load_device("configs/multipair_flexible.json")
rules = StrictRules(device.aod("transport"), device.timing)
moves = (
    MoveRequest(7, Position(0, 0), Position(0, 120)),
    MoveRequest(10, Position(5, 0), Position(2, 120)),
)
assert all(not neighbors for neighbors in build_conflict_map(moves, rules).values())
assert rules.can_fit(moves)
assert rules.duration_us(moves) > device.timing.load_us + device.timing.store_us
```

The transporter builds the pairwise map once per layout transition. It selects
compatible requests subject to row/column capacity and current occupancy.
When a source or target grid intersection contains another pending atom, batch
admission includes that atom and repeats the intersection queries until the
group is closed. If an atom cannot join, that extension is deferred or split.
Occupancy is updated after each completed batch.

Each accepted batch produces one LOAD, a MOVE when positions change, and one
STORE. Multiple rows and columns can be active. The direct template uses no
intermediate offsets; cases requiring staged pickup are split into smaller
batches. Source and destination frames preserve their active line identities
and order. Group duration uses the maximum atom distance, with the same timing
model as sequential transport.

Full movement and state checks are used in tests. The transport constructor
uses its compatibility, capacity, and occupancy rules directly.

## Staged strict transport

`StagedStrictTransport(aod_id, geometry=None)` uses the same transport interface.
Requests are ordered by decreasing movement distance before forming strict
compatible groups. Group admission accounts for line capacity and occupied
destination sites. Staging handles cross-intersections without collecting
unrequested spectator atoms.

The implementation separates:

| Module | Responsibility |
| --- | --- |
| `transport/staged.py` | Distance-ordered groups and transport integration |
| `transport/staging_geometry.py` | Regular SLM pitches and paired gate geometry |
| `transport/staged_policy.py` | Reference regular-array geometry |
| `transport/staged_plan.py` | Cached stages, actual AOD resources, and occupied intersections |
| `transport/staging.py` | Reference row/column transfers and intermediate positions |
| `transport/staged_emission.py` | Complete active grids, affected atoms, and action timing |

Fewer columns than rows selects column pickup and release; fewer rows selects
row pickup and release; equal counts selects row pickup and column release.
Compatible rows or columns with identical occupied masks can transfer together.
Every emitted MOVE preserves active line identity and order. Only atoms whose
positions change appear in its targets; all active lines remain in its grid.
Each transfer and offset contributes its own duration.

Gate-column release uses the source loops for any column count, including
sparse masks. The upward spreading scan corrects the source's lower-boundary
test to use the upper boundary, keeping unselected columns on off-site lanes.
The [recorded counterexample](reference/unsafe_sparse_gate.json) retains the
original collision trace. [ADR 0017](../../docs/decisions/0017-source-routing-audit.md)
records the correction and removal of earlier admission shortcuts.

Regular storage grids and horizontal paired gate arrays are inferred from
Device sites. A singleton axis has no observable pitch: the default is twice
the AOD minimum spacing, while a single gate-pair column uses pair separation
plus three minimum spacings. Supply `StagingGeometry` with explicit
`SiteStagingGeometry` records when the physical lattice pitch is known.
This preserves the existing Device schema.

Regions with the reference regular geometry use staging.
Each region must describe one regular array or a paired gate array with
consistent pitches and bounds; heterogeneous subarrays use direct transport.
Other regions retain the direct strict constructor. AOD-held readout uses
the existing singleton transport. These choices do not change DAG progress,
measurement identity, feedback latency, or pulse emission.

```text
uv run --locked python examples/compile_multipair.py --transport staged --bit 1 --reuse --placement ids
```

The [pinned C++ reference harness](reference/README.md) compares the atom-event
stream of each supplied batch. Independent tests additionally check explicit
AOD grids, every action, and final replay. See
[ADR 0015](../../docs/decisions/0015-staged-strict-transport.md).

Compare transports on the same circuit, device, and measurement case:

```text
uv run --locked python examples/compile_multipair.py --transport sequential --bit 1
uv run --locked python examples/compile_multipair.py --transport strict --bit 1
uv run --locked python examples/compile_multipair.py --transport strict --bit 0
```

## Relaxed routing

`RelaxedTransport(aod_id, geometry=None, prefer_split=1.0)` composes the
reference compatibility and group-merging algorithm with existing staged
transfers. It retains equality on each axis, so rows and columns cannot split
or merge. Endpoint order changes are realized by inserting rows during pickup
and releasing columns in stages; active lines never cross in one MOVE.

The implementation separates `relaxed_rules.py` for compatibility and cubed
offset costs, `relaxed_groups.py` for strict groups and donor redistribution,
`relaxed_policy.py` for Device admission, and `relaxed.py` for transport
composition. The existing staging generator and Atom IR emitter are shared.

The reference starts with distance-descending strict groups and considers
donors in reverse order. It redistributes a donor only when every atom has a
compatible recipient and the sum of incremental proxy costs is low enough.
`prefer_split=1.0` is the reference default; larger values favor strict groups.
Device capacity, workspace, and destination occupancy can also retain a strict
group. No duplicate Atom IR plans or complete action checks are used to make
this decision.

The source template constructs the intermediate coordinates. `StagingPlans`
caches those stages and binds their actual active grids to configured capacity,
spacing, workspace, and occupied intersections. Emission consumes the same
stages. There is no column-count cap, complete-mask requirement, or
count-times-pitch workspace estimate. Empty SLM sites do not block routing.
Paired arrays require positive pair separation smaller than the horizontal
pitch; source and target offsets use their own region's pair separation.

```text
uv run --locked python examples/compile_multipair.py --transport relaxed --bit 1 --reuse --placement ids
uv run --locked python examples/compile_multipair.py --transport relaxed --bit 1 --reuse --placement ids --prefer-split 20
```

The merge proxy uses cube roots of distances as in the source implementation.
It is separate from the Device distance-to-time function used to report actual
program durations. See [ADR 0016](../../docs/decisions/0016-relaxed-routing.md)
and the [executed C++ comparison](reference/relaxed_README.md).

The example reports transport duration, load batch sizes, and maximum parallel
atom count. See [ADR 0011](../../docs/decisions/0011-strict-parallel-transport.md)
for the construction rules.

Historical baseline before ADR 0017, with the example device, pair capacity 2,
and forced measurement values:

| Bit | Sequential transport actions | Strict transport actions | Sequential transport time (us) | Strict transport time (us) |
| --- | ---: | ---: | ---: | ---: |
| 0 | 52 | 22 | 2741.133 | 1340.949 |
| 1 | 76 | 28 | 3861.449 | 1621.157 |

Transport actions count LOAD, MOVE, and STORE, including readout transport.
Strict batches move up to four atoms together in this example. Both
transports produce the same gate decisions, phase, measurement identities
and values, and final layout. These times use the configured study model.

## Flexible device foundation

The device records zones, fixed SLM sites, flexible AOD capabilities, and
transfer/movement timing. `AtomLayout` records atom identities, positions,
holders, active lines, and the optional physical-qubit-to-atom mapping.
An AOD holder binds an atom to one row and one column. Empty active lines
are retained because their intersections can affect other atoms.

Optional `Device.cz`, `Device.local`, and `Device.readout` records provide
control channels, durations, allowed holders, interaction radius, readout
spacing, retention, and result latency. Their JSON keys use the same names.
Placement enforces zone and holder permissions while selecting positions.
The current CZ placers use the SLM-SLM capability.

```python
from nadit_physical_compiler import (
    AODGrid,
    AODLine,
    AtomLayout,
    AtomState,
    Position,
    SLMHolder,
    check_load,
    load_device,
    validate_layout,
)

device = load_device("tests/fixtures/devices/zoned_small.json")
site = device.site("storage_r0_c0")
layout = AtomLayout((AtomState(0, site.position, SLMHolder(site.id)),))
validate_layout(device, layout)

target_grid = AODGrid("transport", rows=(AODLine(0, 0),), columns=(AODLine(0, 0),))
result = check_load(device, layout, target_grid, {0: (0, 0)})
assert result.ok
assert layout.atom(0).holder == SLMHolder(site.id)
assert device.zones_at(Position(5, 245), "measure")
```

Run this example from the module directory. Fixture dimensions, AOD line
spacing, and timing are explicit study values.

| API | Contract |
| --- | --- |
| `load_device(path)` / `loads_device(source)` | Load JSON geometry; `slm_grids` generate fixed sites alongside explicit `slm_sites` |
| `slm_grid(prefix, origin, rows, columns, pitch)` | Generate sites with IDs `{prefix}_r{row}_c{column}` |
| `device.zones_at(position, operation=None)` | Query spatial capabilities independently of trap holders |
| `device.timing.move_duration(distance)` | Linear distance-to-time conversion; distances in um and durations in us |
| `validate_grid(device, grid)` | Check workspace, line capacity, and minimum spacing |
| `validate_layout(device, layout)` | Validate entry-state bindings, occupied positions, and active intersections |
| `check_move(device, layout, target_grid, targets)` | Check final grid coordinates against explicit atom destinations |
| `check_load(device, layout, target_grid, loads)` | Check added lines and atom-to-intersection loading bindings |
| `check_store(device, layout, target_grid, stores)` | Check removed lines and atom-to-SLM-site unloading bindings |

The three transport checks return `ConstraintResult` with `ok`, `rule`, and
`message`; `ok` is a derived property. They leave the layout unchanged.
Use them in tests and explicit debugging of constructed transports. The
default compiler path constructs legal actions from the selected strategy.

MOVE preserves active line identities and spatial order; omitted atoms must
remain stationary. LOAD preserves existing line coordinates and checks the
full set of intersections for unintended SLM pickup. STORE preserves remaining
line coordinates and checks every atom affected by deactivation. These
transfer rules use the reference row/column activation model.

Coordinates use an absolute comparison tolerance of `1e-9` um. Motion checks
evaluate endpoints; later routing supplies intermediate movements. Transport
checks enforce AOD geometry and occupancy. Zone operation permissions are
available for the action compiler to query.

## Atom IR

The public types are `Load`, `Move`, `Store`, `CZPulse`, `Rotation`, `Reset`,
`Measure`, `Wait`, `AtomStep`, and `AtomProgram`. `AtomAction` is their action
type union. Each action has a duration in microseconds and optional source
physical-operation IDs.

```python
from nadit_physical_compiler import (
    AtomProgram,
    AtomStep,
    CZPulse,
    Measure,
    Region,
    ResultRef,
    Wait,
)

program = AtomProgram(
    (
        AtomStep((CZPulse("rydberg", Region(0, 119, 10, 121), duration_us=0.5),)),
        AtomStep(
            (
                Measure(
                    "camera",
                    ((0, ResultRef(9)),),
                    retain_atoms=True,
                    duration_us=10,
                    source_ids=(9,),
                ),
            )
        ),
        AtomStep((Wait(duration_us=3),)),
    )
)
assert program.duration_us == 13.5
assert [start for start, _ in program.timeline()] == [0.0, 0.5, 10.5]
print(program.to_text())
```

All actions within a step use the same entry state and start together.
The next step starts after the longest action completes. Constructors and
text output do not call Device or transport checks.

`apply_step(layout, step, device)` and `replay(program, entry_state, device)`
apply the constructed state transitions without full transport checks. Empty
AOD grids are omitted from the resulting layout. Replay currently supports
retained readout; destructive measurement raises `NotImplementedError`.

`CZPulse` describes illumination, not gate pairs. `Measure` preserves
atom-to-result identity and records whether readout retains the atoms.
Transport actions contain complete target AOD frames and atom bindings.
`to_text()` serializes these records using NADiT's Stim-style syntax.

See the [Atom IR specification](docs/atom-ir-and-components.md) for all fields
and the distinction between source result IDs and Stim record offsets.
Print a complete manually constructed transport/readout example with:

```text
uv run --locked python examples/atom_ir_example.py
```

## Input scope

The supported profile contains standard gates, numeric parameters,
measurement, reset, barrier, and single-bit conditions on earlier measurements.
Custom gate definitions in the same source file are expanded before DAG
construction. Numeric expressions such as `pi / 2` are resolved by the parser.

| Version | Supported measurement conditions |
| --- | --- |
| OpenQASM 2.0 | `if(c==0)` or `if(c==1)`, where `c` is a one-bit register |
| OpenQASM 3.0 | `if(c[0])`, `if(!c[0])`, or equality with `true` / `false`, with an optional `else` |

Conditional bodies have a fixed set of gates, resets, or barriers. OpenQASM 3.0
bit comparisons use boolean literals; the importer does not accept `c[0] == 1`.
Nested conditions, loops, conditional measurements, multi-bit conditions,
conditions on bits without an earlier measurement, opaque operations, and
unbound parameters are outside this profile.

Both text and file inputs accept only the standard library include:
`qelib1.inc` for OpenQASM 2.0 or `stdgates.inc` for OpenQASM 3.0. Arbitrary
external include files and paths are rejected; place custom gate definitions
in the circuit source itself.

## Physical DAG contract

| Field | Meaning |
| --- | --- |
| `num_qubits`, `num_clbits` | Number of flattened quantum and classical wires |
| `nodes` | Operation records in normalized circuit order |
| `edges` | Directed `(predecessor_id, successor_id)` wire and result dependencies |
| `quantum_registers`, `classical_registers` | Register records with `name` and flattened `bits` |
| `global_phase` | Circuit global phase in radians |

Each node has `id`, `source_id`, `name`, `qubits`, `clbits`, `params`, and an
optional `condition`. Node IDs are consecutive from zero in the final native
circuit. Source IDs are assigned after custom-gate expansion and branch
flattening, before native translation; all native children retain that ID.
Qubit and classical-bit indices refer to their respective
flattened wire spaces. Parameters are numeric; gate angles use the parser's
radian convention.

`ResultRef(measurement_id)` identifies one source measurement occurrence.
For a measurement node, `node.result` returns `ResultRef(node.source_id)`.
Changing the target GateSet can change node IDs without changing result IDs.
`Condition(result, value)` requires that result to equal `0` or `1`; the default
value is `1`. The importer binds each condition to the latest preceding
measurement of its source bit. A later write to that bit does not change an
existing reference.

Qiskit supplies wire dependencies, including measurement write ordering and
barriers. The adapter adds an edge from the producing measurement to each
conditioned operation. Both `if` and `else` bodies become ordinary nodes with
opposite conditions; branch-local global phase becomes a conditioned
`global_phase` node. Unconditional global phase remains on the DAG.

Nodes on disjoint wires can remain independent. Device capabilities determine
whether independent operations may share a physical batch.

## Feedback reference tester

The following example selects `h/x/z` as its target gates and forces the
measured bit to `1`. The tester reports the `x` branch as executed and the
`z` branch as skipped:

```python
from nadit_physical_compiler import FakeBackend, GateSet, ResultRef, execute, loads_qasm

dag = loads_qasm(
    """
OPENQASM 3.0;
include "stdgates.inc";
qubit[3] q;
bit[1] c;
c[0] = measure q[0];
h q[2];
if (c[0]) {
    x q[1];
} else {
    z q[1];
}
""",
    gate_set=GateSet(("h", "x", "z")),
)

trace = execute(
    dag,
    FakeBackend(seed=7, overrides={ResultRef(0): 1}),
    measurement_latency=2.0,
)
print(
    [
        (event.operation_id, event.status, event.start, event.end)
        for event in trace.operations
    ]
)
# [(0, 'executed', 0.0, 1.0), (1, 'executed', 0.0, 1.0),
#  (2, 'executed', 3.0, 4.0), (3, 'skipped', 4.0, 4.0)]
print(
    [
        (event.result.measurement_id, event.value, event.available_at)
        for event in trace.measurements
    ]
)
# [(0, 1, 3.0)]
print(trace.duration)
# 4.0
```

`execute(dag, backend=None, *, durations=None, measurement_latency=0.0)`
returns an `ExecutionTrace`. The tester starts operations when their
dependencies are complete and any required result is available. A false
condition produces a zero-duration `skipped` event and releases successors.
In this example, the independent `h` runs while measurement feedback is pending.

Times use abstract units: operations default to one unit, while `barrier` and
`global_phase` default to zero. The `durations` mapping can override times by
operation name. `measurement_latency` is the delay from measurement completion
to result availability. The current tester queries the backend at the
result-delivery event.

`trace.operations` is ordered by operation ID; `trace.measurements` is ordered
by result availability. `trace.duration` includes the final operation completion
or result delivery, whichever is later.

`FakeBackend` combines its seed with each source measurement ID, so results do
not depend on execution order or native gate expansion. `overrides` maps
particular `ResultRef` values to explicit bits. A custom backend can provide
`measure(result: ResultRef) -> int`.
These abstract times and fake bits exercise feedback semantics without a
quantum-state simulation.

## Design records

- [ADR 0002](../../docs/decisions/0002-pluggable-physical-compiler.md):
  pluggable compiler direction.
- [ADR 0003](../../docs/decisions/0003-qasm-physical-dag-frontend.md):
  original static frontend and dependencies.
- [ADR 0004](../../docs/decisions/0004-measurement-feedback.md):
  result references, conditions, and the feedback reference tester.
- [ADR 0005](../../docs/decisions/0005-case-driven-physical-compilation.md):
  compiler-owned progress and case-specific feedback.
- [ADR 0006](../../docs/decisions/0006-device-zones-and-interaction-sites.md):
  zones, interaction positions, and holder-dependent operations.
- [ADR 0007](../../docs/decisions/0007-explicit-physical-and-atom-ir.md):
  separate circuit and atom representations, with construction outside validators.
- [ADR 0008](../../docs/decisions/0008-native-frontend-and-module-contracts.md):
  native translation before DAG construction, stable source identities, and
  module handoffs.
- [ADR 0009](../../docs/decisions/0009-one-pair-compilation.md):
  one-pair stage composition, AOD readout, and compile-time feedback.
- [ADR 0010](../../docs/decisions/0010-multipair-compilation.md):
  multi-pair baseline, shared transport rules, reuse, and search sequence.
- [ADR 0011](../../docs/decisions/0011-strict-parallel-transport.md):
  strict batch admission, occupancy closure, and direct parallel actions.
- [ADR 0012](../../docs/decisions/0012-lookahead-and-matching-reuse.md):
  pure next-CZ preview, feedback boundaries, and matching-based gate anchors.
- [ADR 0013](../../docs/decisions/0013-routing-aware-ids-placement.md):
  source-aligned candidate generation, compatible-group scoring, and IDS.
- [ADR 0020](../../docs/decisions/0020-placement-decision-parity.md):
  first-decision traces for the seven observed source placement differences.
- [Feedback validation](docs/feedback-validation.md):
  the supplied circuit's exhaustive study and reproduction commands.
- [Atom IR and reusable components](docs/atom-ir-and-components.md):
  implemented atom-action contract and proposed component packaging.
