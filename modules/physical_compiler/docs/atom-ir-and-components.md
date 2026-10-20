# Atom IR and Reusable Components

Date: 2026-10-20
Status: Atom IR and component design record; these layers are not implemented. Confirmed directions, proposed contracts, and open details are distinguished below.

## Confirmed direction

- Complete the physical compiler core before implementing reusable component packaging and binding.
- Core compilation preserves Physical DAG dependencies and selects grouping, placement, movement, and timing to produce executable atom actions.
- Components may contain measurement feedback. The initial feedback frontend and fake executor implement result references, single-bit conditions, and dependency release; component packaging will reuse these semantics. See [ADR 0004](../../../docs/decisions/0004-measurement-feedback.md).
- Device configuration covers AOD motion constraints, optional zoning, SLM sites, transfer time, and pulse/addressing capabilities.
- Compare fixed-grid translation with row/column-order-preserving movement. Use distance-to-time functions without velocity dynamics.
- Atom-level output represents already selected parallel actions and supports translation to device commands; AOD intersections and all jointly affected atoms must be accounted for.
- CZ actions describe actual pulses with adjustable illumination regions or local addressing. Single-qubit operations address explicit atoms.
- Reuse must accept multiple participating code blocks, including control and target blocks with independent mappings and positions.

## Confirmed compiler framework direction

- The complete physical compiler is pluggable: each strategy consumes a physical circuit/DAG, Device capabilities, and entry state, and produces executable atom actions.
- Device capabilities guide planning and transport construction throughout the pipeline; they are not only final validation inputs.
- Rigid translation must hold for every intermediate move. Order-preserving row adjustments cannot be reused unchanged by a rigid backend.
- First dissect Search Smarter, Not Harder, then establish the general framework, then implement and compare several compiler strategies.
- The reference's zoning assumptions and internal phases do not fix the common framework. Strategy variants, interfaces, and stage-level pluggability remain open.
- See [ADR 0002](../../../docs/decisions/0002-pluggable-physical-compiler.md). Component packaging remains later work; this record starts no implementation.

## Proposed Atom IR

Use Stim-style instruction text with `LOAD`, `MOVE`, `STORE`, `CZ_PULSE`, `ROT`, `R`, `M`, and `TICK`.
Syntax reference: [Stim circuit format](https://github.com/quantumlib/Stim/blob/main/doc/file_format_stim_circuit.md).
`LOAD`/`STORE` transfer atoms between SLM and AOD; `MOVE` changes the position of an atom already held in AOD. Resource bindings remain explicit or available in execution state.
Actions between `TICK` boundaries start together; the boundary waits for completion, with step duration equal to the maximum action duration.
A CZ pulse identifies a control channel and an illumination region or addressed atom set; the set is not a list of abstract gate pairs. Geometry has already been arranged.
Exact spelling, argument order, measurement-result handling, and `TICK` execution rules remain proposals, not standard Stim semantics or a frozen API.

## Proposed component contract

Keep one parameterized compiled template and bind it to concrete block instances to obtain executable Atom IR.
Block instances supply current role-to-atom mappings, positions, and holders; templates reference local roles, multiple block anchors, and resource parameters.
For compatible code blocks, a schematic call is `TRANSVERSAL_CNOT(control=A, target=B)`; it is a component invocation, not an atom instruction.
A selected transport template may use `destination[i] = target.position[i] + interaction_offset`; which block moves and its grouping belong to that template.
Each template declares entry requirements, required resources and free space, measurement outputs, supported relative layouts, and each block's exit layout/holder state.
Binding substitutes atoms, coordinates, and resources, recalculates duration, and checks the current context, including spectators and AOD intersections.
Reuse without compilation applies when the selected actions and parallel groups remain valid; otherwise the affected part needs a different valid template or replanning.
Changing an anchor does not move existing atoms: relocation requires explicit transport actions. Instance state and measurement results are not reusable template contents.

## Open details

- Device-dependent core contracts, completion criteria, and algorithms. The QASM frontend is implemented as described in [ADR 0003](../../../docs/decisions/0003-qasm-physical-dag-frontend.md) and extended with measurement feedback in ADR 0004.
- Block parameter schema, protocol-defined atom correspondence, supported geometry changes, and binding ownership/failure handling.
- Exact action grammar, resource binding representation, measurement feedback timing, and device-specific legality checks.
