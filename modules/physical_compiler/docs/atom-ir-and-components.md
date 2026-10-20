# Atom IR and Reusable Components

Date: 2026-10-20.
Status: Atom IR structures and text output implemented. Action replay,
physical compilation, and component contracts are subsequent work.

The physical compiler core comes first. It produces an atom-action program
for one measurement case as defined in
[ADR 0005](../../../docs/decisions/0005-case-driven-physical-compilation.md).
Device requirements are in the [device design](device-and-zones.md);
the [implementation plan](implementation-plan.md) defines the next steps.

## Atom actions

`physical_ir.py` defines source operations, dependencies, and result references.
`atom_ir.py` defines concrete atom actions, parallel steps, and programs.
Physical-to-atom compilation connects these two representations.

All records are immutable. Each action has `duration_us` and optional
`source_ids` linking it to physical operations. Positions use um; angles use
radians. The constructor checks finite scalar timing and angle values.
Construction and serialization do not call Device or transport validators.

| Action | Fields beyond timing and source IDs | Meaning |
| --- | --- | --- |
| `Load` | `grid`, `bindings=(atom, row, column)` | Transfer SLM-held atoms into AOD intersections |
| `Move` | `grid`, `targets=(atom, Position)` | Move carried atoms to the listed positions |
| `Store` | `grid`, `bindings=(atom, site_id)` | Transfer AOD-held atoms to fixed SLM sites |
| `CZPulse` | `channel`, `coverage` | Illuminate a `Region` or an explicit tuple of atom IDs |
| `Rotation` | `channel`, `atoms`, `axis`, `angle` | Apply `exp(-i * angle * Pauli_axis / 2)` to each addressed atom |
| `Reset` | `channel`, `atoms` | Reset addressed atoms to the computational zero state |
| `Measure` | `channel`, `results=(atom, ResultRef)`, `retain_atoms` | Z-basis readout with an explicit result identity for each atom |
| `Wait` | No additional fields | Reserve time, including feedback latency |

Bindings, targets, and results are tuples of the listed entries.
`AtomAction` is the union of these eight action types. A transport `grid` is
the complete target AOD frame, including empty active lines; initial positions
and holders come from the entry state.

A CZ action denotes a pulse, with no encoded gate pairing. An atom-ID coverage
tuple specifies the illuminated atoms. Their geometry and Device capabilities
determine interactions and permitted holder combinations. The pulse preserves
holders.

Measurement can address AOD-held atoms without STORE. If `retain_atoms=True`,
the atoms keep their positions and holders. Otherwise they are removed at
measurement completion. Other measurement bases use preceding rotations.
Results carry stable source measurement IDs; sampled bits belong to the
compilation case.

## Parallel steps and programs

`AtomStep(actions)` groups actions that start together. All actions use the
same entry state; their tuple order does not imply sequential execution.
The producer selects a jointly executable group. Step duration is the maximum
action duration, or zero for an empty step.

`AtomProgram(steps, global_phase=0)` orders the steps. Its `duration_us` sums
their durations; `timeline()` returns `(start_us, step)` entries.
`Wait(duration_us=...)` expresses idle time within this timing model.
Circuit global phase is retained as program metadata.

The compiler constructs legal actions using Device-aware transport and pulse
rules. The existing `check_*` functions serve as reference checks in tests and
explicit debugging, outside the default construction path.

## Text output

`AtomProgram.to_text()` writes one action per line and closes each step with
`TICK(duration_us=...)`. Actions before the same TICK start together.
Input tuple order is preserved, resource names use JSON string escaping, and
numbers retain their floating-point representation.

The syntax follows Stim's mnemonic, parameter, target, and TICK layout.
NADiT adds named parameters, geometry, resource bindings, and timed steps;
this text uses its own instruction semantics.

```text
CZ_PULSE(duration_us=0.5,channel="rydberg",region=[0,119,10,121]) # source_ids=7
TICK(duration_us=0.5)
CZ_PULSE(duration_us=0.5,channel="local") 0 1 # source_ids=8
TICK(duration_us=0.5)
M(duration_us=10,channel="camera",retain_atoms=true) 0->rec[9]
TICK(duration_us=10)
WAIT(duration_us=3)
TICK(duration_us=3)
```

`rec[9]` names source measurement 9, rather than an offset from the latest
measurement. `source_ids` and `global_phase` are comment metadata.
Transport lines include the full target frame and atom bindings.

Run the complete manually constructed example from the module directory:

```text
uv run --locked python examples/atom_ir_example.py
```

It shows loading, parallel movement, unloading, a regional CZ pulse,
addressed rotation, and readout while the atoms remain in AOD.
The [Stim format reference](https://github.com/quantumlib/Stim/blob/main/doc/file_format_stim_circuit.md)
describes the source syntax conventions.

## Proposed component contract

A reusable component describes its physical operation, entry conditions,
resources, measurement outputs, and exit layout. Its definition retains
measurement conditions; a case supplies their outcomes.

Block parameters provide atom mappings, positions, and trap holders. Multiple
blocks can participate, for example
`TRANSVERSAL_CNOT(control=A, target=B)` with independent block placements.
A transport template can reference a destination such as
`target.position[i] + interaction_offset`.

Binding substitutes concrete atoms, coordinates, and resources and computes
the resulting durations. Each invocation assigns distinct measurement
identities. Reuse of compiled actions requires their geometry and parallel
groups to remain valid under the supplied bindings; otherwise the affected
part is replanned. Moving a block requires explicit transport actions.

The component schema, representation of conditional variants, protocol-defined
atom correspondence, and binding interface will be designed after the core
pipeline is complete.
