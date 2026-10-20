# Physical Compiler Implementation Plan

Date: 2026-10-20.
Status: Planned. The QASM frontend, Physical DAG, fake results, and feedback
reference tester are implemented.

## Compilation contract

```text
Physical DAG + Device + strategy + entry state + measurement provider
    -> native operations
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
| `src/nadit_physical_compiler/device.py` | Geometry, traps, operation capabilities, timing, configuration loading |
| `src/nadit_physical_compiler/state.py` | Qubit-to-atom mapping, atom positions and holders, occupancy |
| `src/nadit_physical_compiler/motion.py` | Grouped movement legality and the two AOD motion models |
| `src/nadit_physical_compiler/atom_ir.py` | Atom actions, parallel steps, source links, Stim-style text output |
| `src/nadit_physical_compiler/validation.py` | Action replay, state transitions, parallel legality, timing and source coverage |
| `src/nadit_physical_compiler/lowering.py` | Native gate decomposition with source operation and condition provenance |
| `src/nadit_physical_compiler/compiler.py` | Compilation context, ready set, measurement records, strategy contract and result |
| `src/nadit_physical_compiler/strategies/search_smarter.py` | First strategy: scheduling, reuse, placement, routing, action generation |
| `configs/zoned_flexible.json`, `configs/zoned_rigid_16.json` | Flexible reference and rigid-device study configurations |
| `tests/fixtures/devices/zoned_small.json` | Small geometry shared by device and compilation tests |
| `examples/compile_study.py` | QASM-to-Atom-IR study and compilation metrics |
| `tests/test_device.py`, `tests/test_motion.py` | Geometry and grouped movement checks |
| `tests/test_atom_ir.py`, `tests/test_lowering.py`, `tests/test_compiler.py` | Action, native-gate, feedback and pipeline checks |

Keep stage helpers together initially. Split a strategy by stage when its
implementation requires separate files.

## 1. Device, state, and motion capabilities

Implement `device.py`, `state.py`, and `motion.py` from the
[device design](device-and-zones.md). Use ordinary data records, direct calls,
and JSON configuration through the standard library. Add the small device
fixture at `tests/fixtures/devices/zoned_small.json`.

Expose transport checks that receive current state, the selected AOD, and
proposed line activations, transfers, and atom destinations. They evaluate
affected intersections, shared lines, capacity, spacing, and the selected
motion rule. Both models use the same inputs and return legality results
with the violated rule.

Acceptance:

- Load a small explicit zoned fixture with storage, interaction candidates,
  and AOD-readable measurement positions.
- Accept a rigid translation; reject a relative-spacing change under rigid
  motion and accept it under order-preserving motion when its other rules hold.
- Detect a carried atom omitted from a shared-control movement and an
  unintended pickup at an activated intersection.
- Keep region membership independent of holder type.

## 2. Atom IR and action replay

Implement structured LOAD, MOVE, STORE, CZ_PULSE, ROT, reset, and measurement
actions, then their Stim-style text output. Actions in a step start together;
the next step starts after the longest action completes.

`CZ_PULSE` carries its control binding and coverage. Expected source gates
are provenance used to check the interactions produced by the occupied
geometry. `M` carries atom targets and source result identities.

Acceptance:

- Replay transfer, parallel movement, pulse, addressed rotation, and readout
  into a consistent exit state and timeline.
- Validate all three supported CZ holder combinations.
- Measure an atom while it remains in AOD, without inserting STORE.
- Reject unintended illuminated pairings and incompatible actions in a step.

## 3. Native gates and one-pair compilation

Implement `lowering.py`, `compiler.py`, and the first path through
`strategies/search_smarter.py`. Lower supported circuit gates to CZ and
addressed rotations while retaining source IDs, conditions, and global phase.
Use the existing parser and fake result provider.

Complete a small one-pair circuit through placement, transport, CZ, readout,
and a conditional single-qubit operation. This establishes the full input and
output path before optimizing larger layers.

Acceptance:

- Check native decompositions against their source operations.
- Compile both forced measurement branches with the expected source coverage.
- Sample each measurement once and reuse it across all dependent conditions.
- Enforce source dependencies and result latency in generated action times.

## 4. Parallel Search Smarter strategy

Complete the flexible zoned profile using fixed candidate interaction pairs:
gate layering, reuse analysis, routing-aware placement with IDS, transport
batching, and action generation. Start from the pinned reference implementation
linked in the device design.

Routing builds conflicts using Device motion rules and current occupancy.
Pairwise conflicts guide batch selection; the grouped-move check establishes
legality for the complete batch. Loading, offsets, movement, and unloading
are expanded into explicit steps.

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
choices with `configs/zoned_rigid_16.json`. Use the same action and result contracts.
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
The next work item is step 1.
