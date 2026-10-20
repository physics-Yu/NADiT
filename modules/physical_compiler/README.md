# Physical Compiler

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
Physical-DAG-to-Atom-IR compilation and atom-action replay are not implemented.

## Next step

Next, connect one-pair compilation through the module handoffs, with
atom-action replay and Device pulse/readout policies.
The [device design](docs/device-and-zones.md) records the accepted zoning,
interaction-position, and AOD measurement requirements. The
[implementation plan](docs/implementation-plan.md) gives the file paths and
acceptance criteria through the flexible pipeline and rigid-device comparison.

The accepted [compilation design](../../docs/decisions/0005-case-driven-physical-compilation.md)
places DAG progress, the ready set, predicted atom positions and holders, and
planned time inside the physical compiler. Device capabilities determine
whether a candidate batch is legal.

When compiling a measurement, the compiler obtains and stores one fake bit
using the seed and stable source measurement ID. Successor conditions then
select emitted operations or skips and release dependencies. Compilation
decisions advance the DAG directly. Planned times still enforce measurement
completion plus result latency before dependent operations.

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
| `PlacementPlan` | `execution_layout`: layout for the layer; `exit_layout`: desired layout after the layer |
| `AtomFragment` | `steps`: generated AtomSteps; `exit_layout`: resulting state; `completed_operation_ids`: completed native DAG nodes |

`Scheduler.select`, `ReuseAnalyzer.analyze`, `Placer.place`, and
`Transporter.construct` are typing protocols for interchangeable helpers.
Each implementation owns its options. Transport returns existing AtomSteps;
emission binds action parameters and durations. These interfaces do not
implement scheduling, placement, routing, or a compiler loop.

## Flexible device foundation

The device records zones, fixed SLM sites, flexible AOD capabilities, and
transfer/movement timing. `AtomLayout` records atom identities, positions,
holders, active lines, and the optional physical-qubit-to-atom mapping.
An AOD holder binds an atom to one row and one column. Empty active lines
are retained because their intersections can affect other atoms.

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
- [Feedback validation](docs/feedback-validation.md):
  the supplied circuit's exhaustive study and reproduction commands.
- [Atom IR and reusable components](docs/atom-ir-and-components.md):
  implemented atom-action contract and proposed component packaging.
