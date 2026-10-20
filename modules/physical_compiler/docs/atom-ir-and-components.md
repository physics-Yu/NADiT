# Atom IR and Reusable Components

Date: 2026-10-20.
Status: Atom IR syntax and component contracts are proposed.

The physical compiler core comes first. It produces an atom-action program
for one measurement case as defined in
[ADR 0005](../../../docs/decisions/0005-case-driven-physical-compilation.md).
Device requirements are in the [device design](device-and-zones.md);
the [implementation plan](implementation-plan.md) defines the next steps.

## Atom actions

The output records atom identities, transfers, movements, pulses, measurements,
and planned times. Actions in one AtomStep must be executable together under
the selected Device model, including effects on atoms sharing AOD intersections.
Atom IR carries these selected parallel groups and explicit resource bindings
for translation to experimental device commands.

A CZ action denotes the pulse applied to an illumination region or locally
addressed atoms. The compiler arranges the interaction geometry before that
pulse. Single-qubit actions address explicit atoms.

CZ supports the holder combinations permitted by Device: SLM-SLM, AOD-SLM,
and AOD-AOD. A pulse preserves the atoms' holders. Measurement can operate on
AOD-held atoms in a readout region without STORE; its retention rule determines
whether the atom remains in place or is removed.

## Proposed notation

Use Stim-style instruction text with `LOAD`, `MOVE`, `STORE`, `CZ_PULSE`,
`ROT`, `R`, `M`, and `TICK`. The
[Stim format](https://github.com/quantumlib/Stim/blob/main/doc/file_format_stim_circuit.md)
is the syntax reference.

- `LOAD` and `STORE` transfer atoms between SLM and AOD.
- `MOVE` changes the position of atoms held in AOD.
- `CZ_PULSE` identifies a control channel and illumination region or atom set.
- `ROT`, `R`, and `M` identify the addressed atoms.
- Actions between `TICK` boundaries start together. Step duration is the maximum
  duration of its actions; the next step follows their completion.

Resource bindings, source-operation links, and measurement identities accompany
the actions. Instruction spelling and argument encoding will be selected with
the compiler core.

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
