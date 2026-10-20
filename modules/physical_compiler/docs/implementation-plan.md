# Physical Compiler Implementation Plan

Date: 2026-10-20.
Status: Steps 1 and 3, retained-readout replay and operation capabilities from
step 2, and the multi-pair correctness baseline in step 4 are implemented.
Step 4's shared rules and direct strict batching are also implemented.
The next increment is following-layer preview and matching-based reuse.

## Compilation contract

```text
QASM + GateSet
    -> parsed circuit
    -> native translation
    -> PhysicalDAG

Physical DAG + Device + strategy + entry state + measurement provider
    -> gate layers
    -> reuse analysis
    -> placement
    -> transport batches
    -> Atom IR + timeline + measurement records + exit state
```

The compiler owns the ready set and predicted state. Compiled or skipped
predecessors release successors for planning. A measurement obtains one fake
result under its stable source identity; conditions select the emitted path.
Planned times enforce dependencies and feedback latency.

The intended entry point is:

```text
compile_physical(dag, device, *, strategy, entry_state, measurement_backend)
    -> CompilationResult
```

The strategy receives the compilation context and Device capabilities.
Stage helpers belong to the strategy; the shared contract is the complete
compilation input and output.

## File ownership

Paths below are relative to `modules/physical_compiler/`. Create files when
their implementation step begins.

| Path | Responsibility |
| --- | --- |
| `src/nadit_physical_compiler/physical_ir.py` | Physical operations, dependencies, conditions, and stable result references |
| `src/nadit_physical_compiler/device.py` | Geometry, traps, operation capabilities, timing, configuration loading |
| `src/nadit_physical_compiler/capabilities.py` | CZ, local control, and readout channel/holder/timing records |
| `src/nadit_physical_compiler/state.py` | Qubit-to-atom mapping, atom positions and holders, occupancy |
| `src/nadit_physical_compiler/motion.py` | Flexible AOD movement and line-controlled transfer checks; rigid rules follow in step 6 |
| `src/nadit_physical_compiler/atom_ir.py` | Atom actions, parallel steps, source links, Stim-style text output |
| `src/nadit_physical_compiler/validation.py` | Explicit test/debug validation of action legality and source coverage |
| `src/nadit_physical_compiler/replay.py` | Action state transitions and timing |
| `src/nadit_physical_compiler/frontend/` | Source parsing/expansion, native translation, and final DAG construction |
| `src/nadit_physical_compiler/contracts.py` | Gate-layer, placement, and Atom IR fragment handoffs |
| `src/nadit_physical_compiler/compiler.py` | Compilation context, ready set, measurement records, strategy contract and result |
| `src/nadit_physical_compiler/strategies/search_smarter.py` | First strategy: scheduling, reuse, placement, routing, action generation |
| `src/nadit_physical_compiler/scheduling/` | Layer selection and reuse analysis |
| `src/nadit_physical_compiler/placement/` | Candidate locations, cost evaluation, and IDS |
| `src/nadit_physical_compiler/transport/` | Shared compatibility/cost rules, batching, and legal transport construction |
| `src/nadit_physical_compiler/emission/` | Lightweight Atom IR parameter and timing binding |
| `configs/zoned_flexible.json`, `configs/zoned_rigid_16.json` | Flexible reference and rigid-device study configurations |
| `tests/fixtures/devices/zoned_small.json` | Small geometry shared by device and compilation tests |
| `examples/compile_study.py` | QASM-to-Atom-IR study and compilation metrics |
| `examples/atom_ir_example.py` | Manually constructed atom program and text output |
| `examples/compile_pair.py`, `examples/pair_feedback.qasm3` | Executable one-pair compilation and feedback example |
| `configs/one_pair_flexible.json` | Explicit geometry and operation capabilities for the one-pair study |
| `src/nadit_physical_compiler/placement/catalog.py`, `placement/multipair.py` | Fixed pair catalogue, multi-CZ allocation, and storage returns |
| `src/nadit_physical_compiler/transport/sequential.py` | Singleton transfers with destination vacancy ordering |
| `src/nadit_physical_compiler/transport/rules.py` | Shared strict geometry, conflict map, endpoint frames, and cost |
| `src/nadit_physical_compiler/transport/occupancy.py`, `transport/strict.py` | Current occupancy, batch closure, and direct parallel actions |
| `src/nadit_physical_compiler/emission/layer.py` | One region pulse for a complete placed CZ layer |
| `examples/compile_multipair.py`, `examples/multipair_feedback.qasm3`, `configs/multipair_flexible.json` | Multi-pair compilation, partner changes, and conditional CZ study |
| `tests/test_device.py`, `tests/test_state.py`, `tests/test_motion.py` | Geometry, atom state, and grouped transport checks |
| `tests/test_atom_ir.py`, `tests/test_native_frontend.py`, `tests/test_contracts.py`, `tests/test_compiler.py` | Action, frontend, handoff, feedback, and pipeline checks |

Stage algorithms live in their respective packages; the strategy composes
them. Create each package when its implementation begins.

## 1. Device, state, and motion capabilities

Implemented: `device.py`, `state.py`, and `motion.py` provide the flexible
reference's geometry, state, and transport checks. JSON loading uses the
standard library. `tests/fixtures/devices/zoned_small.json` supplies a small
study device. The [device design](device-and-zones.md) defines the broader
target capabilities.

`validate_layout` checks an entry state. `check_load`, `check_move`, and
`check_store` evaluate proposed target grids and atom bindings against that
state, returning `ConstraintResult`. They are reference checks for testing
and debugging, outside the default compilation path, and preserve their inputs.
LOAD adds lines, MOVE repositions existing lines, and STORE removes lines.

Acceptance:

- Load a small explicit zoned fixture with storage, paired gate SLM sites,
  and a measurement region without SLM traps.
- Accept parallel moves that change line spacing while preserving line
  identity, order, minimum spacing, and workspace bounds.
- Detect a carried atom omitted from a shared-control movement and an
  unintended pickup at an activated intersection.
- Detect an unintended release when an unloaded atom shares a deactivated line.
- Keep region membership independent of holder type.

CZ, local-control, and readout capability records are implemented in step 2.
Rigid constraints are implemented with the rigid profile in step 6.

## 2. Atom IR and action replay

Implemented: `atom_ir.py` defines transport, pulse, rotation, reset,
measurement, and wait actions, parallel steps, and a program with timing and
Stim-style text output. Physical circuit records live in `physical_ir.py`.
Actions in a step start together; the next step starts after the longest
action completes.

`CZ_PULSE` carries its control binding and coverage. Expected source gates
are provenance used to check the interactions produced by the occupied
geometry. `M` carries atom targets and source result identities.

Implemented: direct action replay and Device CZ/local/readout capability records.
The one-pair placer uses zones, holder permissions, interaction radius, and
readout spacing while constructing placements. Replay applies constructed
actions; full transport checks remain in tests and debugging.

Current replay and construction acceptance:

- Replay transfer, parallel movement, pulse, addressed rotation, and readout
  into a consistent exit state and timeline.
- Measure in AOD without a preceding STORE, then return to the previous SLM site.
- Check each constructed transport and intended SLM-SLM pulse geometry in tests.

Remaining: general pulse/parallel-action validation, AOD-held CZ strategies,
and destructive-readout lifecycle handling. Replay supports retained atoms.

## 3. Native frontend, module handoffs, and one-pair compilation

Implemented: frontend native translation before PhysicalDAG construction,
with a configurable GateSet defaulting to CZ and rotations. Source measurement
IDs are separate from final node IDs, and conditioned phase contributions keep
their guards. `contracts.py` defines gate-layer, placement, and AtomStep
fragment records, with scheduler, reuse, placement, and transport protocols.

Implemented: `compiler.py` owns the ready set, predicted state, time, and
measurement records. `strategies/search_smarter.py` composes the scheduling,
placement, transport, and emission modules through the existing handoffs.
Each module owns its options and fragments contain existing AtomSteps.

`examples/compile_pair.py` compiles a two-atom QASM circuit through placement,
transport, CZ, AOD readout, and conditional single-qubit operations. It checks
replay against the compiler's exit state. Both forced measurement branches are
covered in integration tests.

Acceptance:

- Check native decompositions against their source operations.
- Compile both forced measurement branches with the expected source coverage.
- Sample each measurement once and reuse it across all dependent conditions.
- Enforce source dependencies and result latency in generated action times.

## 4. Parallel Search Smarter strategy

Implement this stage in five increments, as defined in
[ADR 0010](../../../docs/decisions/0010-multipair-compilation.md):

1. Fixed pair catalogue, capacity-limited multi-CZ scheduling, coordinated
   placement, sequential transport, and whole-layer pulses.
2. Shared movement compatibility/cost rules and strict parallel transport.
3. Following-layer preview and matching-based reuse.
4. IDS placement search.
5. Relaxed routing and its staged action construction.

Items 1 and 2 are implemented. Integration tests cover both forced feedback
branches, actual illuminated interaction pairs, capacity splitting, spectator
clearing, and replay. Strict transport constructs simultaneous LOAD/MOVE/STORE
batches; capacity, source ghosts, and target occupancy can split a batch.
Staged pickup and offsets are subsequent constructors. Gate participants return
to storage after CZ; reuse is added in item 3, which is next.

Complete the flexible zoned profile using fixed candidate interaction pairs:
gate layering, reuse analysis, routing-aware placement with IDS, transport
batching, and action generation. Start from the pinned reference implementation
linked in the device design.

Routing constructs compatible batches using Device rules and current occupancy.
Pairwise conflicts guide selection; incremental resource accounting handles
batch capacity. The strategy expands selected batches using legal loading,
offset, movement, and unloading rules. Reference checks test the construction
rules rather than rechecking every candidate in the default search path.

Reuse analysis supplies placement preferences; placement selects final
positions and holders. Occupancy and pulse coverage determine whether retained
atoms can remain through the next layer.

Acceptance:

- Compile a multilayer circuit with parallel gates and atom reuse.
- Cover shared-line constraints, row/column order, and ghost intersections.
- Check each intermediate action produced by relaxed routing.
- Exercise gate capacity limits and split layers when required.

## 5. End-to-end study

Add `examples/compile_study.py` and `configs/zoned_flexible.json`.
Compile representative static circuits and the supplied `logical.qasm3`
fixture, including seeded and forced feedback cases.

Report compilation time, AtomStep count, transport batch count, transfer
count, movement distance, and planned duration. Preserve source links and
measurement values so a result can be inspected and reproduced.

Acceptance:

- Action replay agrees with the compiler's exit state.
- Chosen source operations are covered exactly as specified by lowering;
  skipped operations contribute no actions.
- Existing feedback reference results agree with branch selection.
- A fixed configuration and seed produce repeatable action output.

## 6. Rigid and shared-zone variants

After the flexible pipeline passes the study, implement placement and transport
choices with `configs/zoned_rigid_16.json`, including rigid grouped-motion
checks. Use the same action and result contracts.
Compare equal circuits, geometry, entry states, and measurement cases. Add a
`configs/zoned_flexible_16.json` control when separating motion restrictions
from capacity.

Then exercise overlapping storage/entanglement regions and planning that
retains atoms in AOD for gates. Component packaging follows the complete
physical compiler.

## Verification per step

Add deterministic tests for each implemented contract, then run from the
module directory:

```text
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked mypy .
uv run --locked pytest -q
```

Commit each passing step with its configuration, tests, and API documentation.
The next work item is following-layer preview and matching-based reuse.
Placement search then uses the shared transport compatibility and cost.
