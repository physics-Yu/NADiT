# NADiT Architecture

Date: 2026-10-20.

## Goal

Build a configurable neutral-atom compiler and digital twin. The development
path is logical circuit -> QEC components -> physical circuit -> atom actions.
The current task is the physical compiler: produce a complete atom-action
program for one measurement case under a selected Device and compiler strategy.

## Current state

| Part | State |
| --- | --- |
| OpenQASM 2.0 / 3.0 frontend and Physical DAG | Implemented |
| Configurable native translation before PhysicalDAG | Implemented |
| Scheduling, placement, and transport handoff contracts | Implemented |
| Measurement-result references and single-bit conditions | Implemented |
| Seeded fake measurement backend | Implemented |
| DAG feedback reference tester (`execute`) | Implemented |
| Flexible Device geometry, atom state, and transport constraints | Implemented |
| Atom IR structures, step timing, and text output | Implemented |
| Atom-action state replay with retained readout | Implemented |
| One-pair DAG-to-Atom-IR compilation with feedback | Implemented |
| Multi-pair fixed placement, sequential transport, and shared pulses | Implemented |
| Shared transport rules and direct strict parallel batches | Implemented |
| Reuse, IDS, and staged/relaxed routing | Next |
| Reusable components and logical QEC compilation | Follow the physical compiler |

The module [README](modules/physical_compiler/README.md) defines the implemented API.
The [feedback study](modules/physical_compiler/docs/feedback-validation.md)
covers all 512 assignments to the supplied circuit's nine measurement results.

## Physical compilation

```text
QASM + GateSet
    -> parse and expand source gates/conditions
    -> native translation
    -> PhysicalDAG

Physical DAG + Device + strategy configuration + entry state
    -> compile one case using fake measurement results
    -> Atom IR + timeline + exit state
```

The compiler maintains DAG compilation progress, the ready set, predicted atom
positions and trap ownership, and planned operation times and resource use.
The strategy selects compatible operations and performs gate grouping,
placement, transport planning, and action generation.
Initial mapping and placement are optimization decisions; an entry state
supplies existing assignments and positions.

The frontend completes native gate translation before building PhysicalDAG.
GateSet selects the native basis without supplying zone or AOD geometry.
Source measurement identities survive translation even when DAG node IDs
change. See [ADR 0008](docs/decisions/0008-native-frontend-and-module-contracts.md).

A predecessor releases its successors after it has been compiled or skipped.
When compiling a measurement, the compiler obtains one result from the fake
backend and records it under that source measurement's identity. Later
conditions use this value to select or skip their operations. The original
DAG retains every branch.

Generated times preserve operation dependencies and measurement feedback
latency. The compiler advances through planned operations without waiting for
execution events. See [ADR 0005](docs/decisions/0005-case-driven-physical-compilation.md).

A gate group is a set of selected circuit operations. A transport batch is a
set of compatible movements. An AtomStep is a set of actions scheduled to
start together. A gate group can require several transport batches and steps.

`physical_ir.py` defines the native circuit; `atom_ir.py` defines concrete
atom actions. Strategies construct legal groups from Device rules.
Reference validators serve tests and explicit debugging, outside the default
construction path. [ADR 0007](docs/decisions/0007-explicit-physical-and-atom-ir.md)
records these contracts.

## Device requirements

| Capability | Requirement |
| --- | --- |
| AOD motion | Configure workspace, row/column capacity, and line spacing. Support rigid translation and order-preserving motion for every intermediate move. |
| AOD intersections | Account for every atom affected by activated row/column intersections. |
| SLM sites | Represent fixed trapping sites; track holders and transfer feasibility and cost during planning. |
| Zoning | Configure bounds and operation capabilities for storage, entanglement, and measurement; allow overlapping regions. |
| CZ pulses | Configure interaction distances separately from illumination. Support SLM-SLM, AOD-SLM, and AOD-AOD holder pairs. |
| Single-qubit operations | Address explicit atoms. |
| Measurement | Configure readout positions, addressing, holders, and retention. Permit measurement in AOD without STORE. |
| Timing | Configure transfer, pulse, reset, measurement, and feedback times; compute movement time from distance. |

Device capabilities constrain placement, transport grouping, and action
expansion. The compiler uses a distance-to-time model for motion.

The first strategy assigns atoms dynamically to fixed candidate interaction
pairs. Device legality also supports strategies that generate candidate
coordinates. The [device design](modules/physical_compiler/docs/device-and-zones.md)
defines the single-row gate geometry, 5 um storage grid, and flexible and
rigid profiles. [ADR 0006](docs/decisions/0006-device-zones-and-interaction-sites.md)
records this separation.

The complete compiler strategy is replaceable. Strategies share input and
Atom IR contracts and implement the Device models they support. Device
selection and strategy selection are separate configuration choices.

## Measurement cases and state

The fake backend supplies deterministic test bits from a seed and stable
source measurement identity. Repeated reads share one result. Gate
decomposition, placement, and routing preserve that identity.

The implemented IR uses `0/1`. A provider using `+1/-1` maps `+1` to `0` and
`-1` to `1`. Results belong to a case; they are not part of reusable templates.

The existing `execute` function checks DAG conditions and dependency timing
with abstract completion and result events. It remains a reference tester.
The compiler consumes the result provider directly. Atom IR replay applies
the generated transfers and movement to reconstruct the exit state.

The first strategy supports at most two atoms: fixed SLM CZ pairs, grouped
same-row transport or singleton transfers, and retained readout in AOD.
Readout returns the atom to its preceding SLM site. This leaves a common
SLM boundary for subsequent layers. See
[ADR 0009](docs/decisions/0009-one-pair-compilation.md).

The multi-pair baseline uses an explicit catalogue of isolated SLM pairs.
The scheduler selects disjoint CZ gates up to its capacity. Placement clears
pulse-region spectators, assigns distinct pairs, and returns CZ participants
to storage afterward. Sequential transport moves one atom at a time; one CZ
pulse executes the whole placed layer. The compiler and measurement protocol
remain shared with the one-pair strategy.

StrictTransport replaces the sequential constructor through the same interface.
It uses a movement conflict map plus batch capacity and current occupancy.
Occupied grid intersections are closed over compatible pending requests before
emitting common LOAD/MOVE/STORE phases. Source measurement identities and
branch decisions remain unchanged. See
[ADR 0011](docs/decisions/0011-strict-parallel-transport.md).

## Development order

1. Implement Device, atom state, and flexible transport constraints (complete).
2. Implement Atom IR structures, text output, and retained-readout replay (complete).
3. Implement native frontend translation, module handoffs, and one-pair case
   compilation (complete).
4. Implement the multi-pair baseline and direct strict transport (complete),
   then reuse, IDS placement, and staged/relaxed routing.
5. Implement the rigid 16-by-16 variant and compare shared-zone layouts.
6. Package reusable components and connect logical QEC compilation.

The [implementation plan](modules/physical_compiler/docs/implementation-plan.md)
specifies file ownership and acceptance criteria for these steps.
[ADR 0010](docs/decisions/0010-multipair-compilation.md) defines conflict-map
semantics and the multi-pair module sequence.

Device-aware compilation must emit the chosen source operations as legal atom
actions, preserve their dependencies and case decisions, and return the
resulting layout and planned timeline. Test/debug validation checks source-operation
coverage, movement legality, pulse addressing, and dependency timing.

## Components and logical compilation

The logical compiler selects QEC protocols and components for logical
operations. Components must accept block identities and placements, including
multiple participating blocks such as control and target blocks of a CNOT.

The proposed component contract describes entry conditions, resources, measurement
outputs, and exit layout. Binding supplies concrete atoms, positions, and
resource choices. Reuse applies while its action structure and parallel groups
remain valid for those bindings. See the
[Atom IR and component design](modules/physical_compiler/docs/atom-ir-and-components.md).

QEC protocols provide syndrome-extraction, correction, atom-refresh, and
leakage-handling circuits. Logical measurement results are derived from the
physical measurement records. Leakage handling produces an erasure record
and refreshes or replaces the carrier. Protocol selection and decoding remain
later work. Motivating workloads include PBC and magic-state distillation.

Noise models will consume atom actions, their timing, and measurement records.
A state-aware simulation backend can later supply measurement outcomes for
noise-dependent cases.

## References

- [Search Smarter, Not Harder](https://arxiv.org/html/2512.13790v1): scheduling,
  reuse analysis, placement, routing, and code generation. IDS improves the
  placement search; relaxed routing changes transport construction.
- [MQT QMAP v3.5.0 compiler](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/include/na/zoned/Compiler.hpp):
  compile-time planning through those stages.
- [Enola](https://github.com/UCLA-VAST/Enola/blob/2944dbf4e163e8d2eeeec607add0d9139edce689/enola/enola.py)
  and [ZAC](https://github.com/UCLA-VAST/ZAC/blob/e5083362f99e6915f20c2bd0eaa88b6d1cdcecac/zac/scheduler/scheduler.py):
  additional scheduling and transport references.
- [Circuit-based leakage-to-erasure conversion](https://arxiv.org/abs/2405.10434):
  a reference for carrier-refresh components.

## Repository

[modules/physical_compiler/](modules/physical_compiler/README.md) contains the
Python package, tests, design notes, configurations, and study examples.
Shared decisions are in `docs/decisions/`; engineering rules are in
[agent.md](agent.md).
