# NADiT Architecture

Date: 2026-10-20
Status: QASM frontend and fake measurement-feedback execution implemented; device compilation remains design work.

This document records the agreed direction and the questions that remain open.
**Confirmed** records decisions accepted in the discussion.
**Proposed** records suggestions awaiting a design decision.
**Open** records details that have not been selected.
Engineering rules are preserved in [agent.md](agent.md).

## 1. Purpose and configuration

**Confirmed.** Start from a logical circuit and simulate its execution on a
configurable neutral-atom platform. The central output is the actual atom-action
record for one execution case, including movement and pulse operations.

Configuration includes the QEC code and protocol, compiler and scheduling
strategies, fake experiment backend, device parameters and constraints, and
physical noise models. Device configuration must represent the relevant AOD,
SLM, movement, gate, measurement, and control limitations.

The initial system uses a fake backend. Its execution and feedback interfaces
must leave room for a future real-device backend. Noise backends should be able
to consume the atom-action record.

## 2. Overall structure and build order

**Confirmed.** Build the physical compiler first so that the logical QEC
compiler can use compiled components. QEC protocols define the physical
circuits that implement those components.

**Confirmed on 2026-10-20.** Complete the physical compiler core before
implementing component packaging and binding. The
[module design record](modules/physical_compiler/docs/atom-ir-and-components.md) preserves the requirements
and proposed interfaces; core completion criteria remain open.

**Confirmed on 2026-10-20.** The complete physical compiler is pluggable.
Each strategy receives Device capabilities and completes physical-circuit-to-atom-action
compilation. Capabilities constrain planning and every generated action.
See [ADR 0002](docs/decisions/0002-pluggable-physical-compiler.md).

The component preparation path is:

```text
QEC protocol / basic-operation definition
    -> physical circuit and Physical DAG
    -> physical compiler: gate grouping, placement, movement, and timing
    -> reusable compiled component / Atom IR
```

The feedback-driven execution path is:

```text
Logical circuit -> Logical DAG -> logical QEC compiler
                                      |
                       select and instantiate components
                                      |
                      physical compilation / scheduling
                                      |
                            executable atom actions
                                      |
                           fake experiment backend
                                      |
                       measurement and completion events
                                      |
                      update execution state and continue
```

Compilation and execution advance as feedback becomes available. The complete
action record is accumulated over the run.

**Open.** The exact boundary between runtime component binding, additional
physical compilation, and scheduling remains to be specified.

## 3. Logical QEC compilation and components

**Confirmed.** The Logical DAG expresses dependencies between logical
operations. The logical QEC compiler decides which operation to perform next
and how to realize it using available components, feedback, placement, and
resource information.

The motivating examples include Pauli-based computation (PBC), magic-state
distillation, and measurement-conditioned corrections.

Basic gate units, including the S correction used in the discussion, are
prepared in advance. Runtime work selects, binds, and schedules those units
for the current case.

The QEC protocol must account for syndrome extraction (SE) and the chosen
atom-refresh and leakage-handling operations. Their physical implementations
are inputs to the physical compiler.

**Proposed.** Describe each compiled component by its physical circuit,
required entry conditions, resources, measurement outputs, and exit state or
layout. The exact component contract is still open.

**Open.** Select the SE circuit, refresh protocol, leakage-detection protocol,
and rules for inserting them. No particular code distance, SE frequency,
distillation circuit, or leakage-handling sequence has been selected.

A leakage-handling component must define how detection produces an erasure
record as well as how it refreshes or replaces the physical carrier.
SWAP-based leakage detection is a relevant protocol example; the detailed
implementation is not yet chosen.

## 4. Physical DAG and gate layers

**Confirmed.** The Physical DAG contains
the physical operations and dependencies supplied by the component or QEC
protocol. Physical compilation must preserve the required ordering.

**Implemented on 2026-10-20.** The OpenQASM 2.0 / 3.0 frontend uses
Qiskit parsing and DAG conversion to produce independent NADiT records.
The records retain operations, quantum and classical wire dependencies,
registers, and global phase. Device-dependent scheduling remains a later step.
See [ADR 0003](docs/decisions/0003-qasm-physical-dag-frontend.md) and the
[module API](modules/physical_compiler/README.md).

Single-bit measurement conditions extend that input profile through
[ADR 0004](docs/decisions/0004-measurement-feedback.md). Each condition reads
a particular measurement result, even when a classical bit is later overwritten.

**Proposed terminology:**

- A candidate set, or frontier, contains operations whose required
  predecessors and classical conditions permit them to be considered.
- A gate layer or stage contains operations selected for joint execution.
- A movement batch contains compatible atom movements. One gate stage may
  require several movement batches.

**Open.** Define how the candidate set and layers are maintained and updated,
what counts as operation completion, how measurements unlock successors, and
whether work is submitted as complete layers or as smaller ready subsets.
The Logical DAG's own progress and layer rules also remain open.

The ZAC and Enola studies inform this physical-layer discussion. Their gate
stages are formed during compilation; they do not supply the feedback-driven
logical execution mechanism proposed for NADiT.

## 5. Feedback and execution cases

**Confirmed.** A seed belongs to the fake measurement backend. One seed
identifies a reproducible execution case and drives a sequence of simulated
measurement outcomes.

The compiler receives measurement events through the backend interface. It
then chooses and compiles or schedules the required continuation. For example,
a measurement can select a component that applies an S correction.

Each case produces its own complete executed action record. Branches do not
have to reconverge to a previously fixed schedule. The compiler should learn
a result when the backend reports it, rather than receive all future outcomes
at the start of the run.

**Implemented on 2026-10-20.** A fake backend produces bits from a seed and
measurement ID, or explicit test outcomes. The dependency executor publishes
results after measurement completion plus a configurable reporting latency,
evaluates conditions, and records executed or skipped operations. Time is
abstract; this increment does not implement device scheduling or quantum-state
simulation. See the [feedback validation](modules/physical_compiler/docs/feedback-validation.md).

**Open.** Device timing and resources, component-instance result binding, and
deriving logical results from physical measurements remain to be designed.

## 6. Mapping and placement

**Confirmed.** Mapping assigns circuit-qubit roles to concrete code blocks or
physical qubits. Placement describes where those carriers are located.

Initial mapping is an optimization decision. A solver should use logical
interaction and dependency requirements together with physical feasibility
and execution-cost information to choose it.

The system must retain the adopted mapping, code-block placement, and current
atom positions. Their update rules must support the chosen refresh and
replacement protocols.

**Open.** The optimizer, cost function, initial-placement strategy, and later
movement decisions are deferred. A specific optimization algorithm or fixed
mapping policy has not been selected.

## 7. Execution state, time, and resources

**Confirmed requirement.** Execution needs logical progress, received
measurement results, current mappings and positions, time information, and
resource availability. Time and resource management need an explicit design.

**Proposed mechanism.** A runtime manager maintains:

- A simulation clock.
- Resource reservations and occupation intervals.
- Events such as movement completion, gate completion, and result availability.

The compiler proposes work using the current state. The manager checks and
records resource use; the backend supplies execution events; those events
update the state used by the next compiler decision.

Measurement completion and result availability may occur at different times.
A dependent branch waits for the result-availability event. For example, a
measurement ending at 100 microseconds and reporting at 120 microseconds
would unlock its conditional successor at 120 microseconds. These numbers
illustrate semantics, not device parameters.

**Proposed implementation.** Discrete-event simulation advances the clock to
the next event. A later hardware backend would supply actual completion and
measurement events through the corresponding interface.

**Open.** Decide scheduler and runtime-manager ownership, resource granularity,
compatibility rules, reservation semantics, and whether compilation or
classical processing latency contributes to simulated execution time.

## 8. Atom-action records and noise

**Confirmed.** Preserve the actual atom-action sequence for each case so that
noise backends can analyze its physical execution.

**Proposed record content.** Include atom identities, movement trajectories,
operation timing, pulse or gate descriptions, measurement events, and the
connection between feedback and selected actions. Waiting intervals should
be recoverable from the timeline.

**Proposed extension.** Start with a noise backend that reads completed action
records. If noise must affect measurements and later branches, a state-aware
noisy simulation backend can provide measurement events during execution.

**Open.** Select the action schema, noise representation, required outputs,
and quantum-state simulation method, if needed. Fake random measurement
feedback alone does not define a physical measurement distribution.

## 9. Reference findings

These findings were checked against the papers and official source during
the discussion. They inform the design without selecting an implementation.

- **Enola**, commit `2944dbf4e163e8d2eeeec607add0d9139edce689`:
  dependency-respecting circuits use ASAP gate staging; commuting gate
  groups can use interaction-graph edge coloring. Placement and routing
  follow staging, and routing can create several movement batches per stage.
  See [paper Section III](https://arxiv.org/html/2405.15095v2#S3) and
  [compiler source](https://github.com/UCLA-VAST/Enola/blob/2944dbf4e163e8d2eeeec607add0d9139edce689/enola/enola.py).
- **ZAC**, commit `e5083362f99e6915f20c2bd0eaa88b6d1cdcecac`:
  the scheduler supports ASAP with dependencies and edge coloring when the
  input is declared dependency-free. It splits stages to respect
  entanglement-zone capacity. Subsequent instruction scheduling considers
  qubit, site, and AOD dependencies and assigns start and end times.
  See [scheduler source](https://github.com/UCLA-VAST/ZAC/blob/e5083362f99e6915f20c2bd0eaa88b6d1cdcecac/zac/scheduler/scheduler.py)
  and [paper Section VI](https://arxiv.org/html/2411.11784v2#S6).
- **SWAP-LDU** is a circuit-based example of leakage detection and carrier
  refresh. See [Circuit-Based Leakage-to-Erasure Conversion in a
  Neutral-Atom Quantum Processor](https://arxiv.org/abs/2405.10434).

## 10. Next discussion and collaboration scope

**Confirmed direction on 2026-10-20.** First dissect Search Smarter, Not Harder,
then establish the general physical compiler framework and implement several strategies.
**Open.** Specific strategies, interfaces, and implementation acceptance criteria.
Implementation starts only after its requirements and plan are agreed.

Keep discussions small enough to review and absorb. Analyze and revise the
idea before expanding it into detailed content. Include statements that
advance the reasoning; avoid defensive filler.

For the initial documentation baseline, the user approved content, consistency,
and diff checks as the commit gate. The physical compiler module now has a test
suite; implementation changes follow the standing rules in `agent.md`.

## 11. Repository organization

**Confirmed on 2026-10-20.** Use
[modules/physical_compiler/](modules/physical_compiler/README.md) as the physical
compiler workspace, with `src/`, `tests/`, `docs/`, and `configs/` for implementation,
tests, module design notes, and example device configurations. The module README
is its entry point. Its Python package and frontend layout are defined in
[ADR 0003](docs/decisions/0003-qasm-physical-dag-frontend.md).
Global rules and architecture stay at the root; shared decisions stay in
`docs/decisions/`. See [ADR 0001](docs/decisions/0001-module-workspaces.md).
