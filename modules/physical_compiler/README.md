# Physical Compiler

This module provides an OpenQASM 2.0 / 3.0 frontend that produces a
device-independent Physical DAG, including single-bit measurement feedback.
It parses the circuit with Qiskit, expands custom gate definitions, and adapts
the standard circuit DAG into a small NADiT data model. A small execution loop
can exercise dependencies and conditions using reproducible fake results.

The frontend preserves circuit operations and dependencies. It does not select a
device, lower gates to native pulses, choose parallel layers, or generate atom
actions.

## Layout

```text
physical_compiler/
├── README.md
├── pyproject.toml
├── uv.lock
├── src/nadit_physical_compiler/
│   ├── __init__.py
│   ├── qasm.py       # QASM parsing and DAG adaptation
│   ├── ir.py         # Operations, dependencies, and result references
│   ├── execution.py  # Dependency execution with fake measurement feedback
│   └── py.typed      # Package typing marker
├── tests/        # Frontend, IR, and execution tests
├── docs/         # Module design notes
└── configs/      # Example device configurations
```

This module belongs to the shared NADiT repository, with no separate Git
repository. Empty directories retain `.gitkeep` files.

Read the root [engineering standards](../../agent.md) and
[architecture](../../architecture.md) before working here.

## Setup and verification

Use Python 3.12 and run these commands from `modules/physical_compiler/`:

```text
uv sync --locked
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy .
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

print([(node.id, node.name, node.qubits, node.clbits) for node in dag.nodes])
# [(0, 'h', (0,), ()), (1, 'cx', (0, 1), ()),
#  (2, 'measure', (0,), (0,)), (3, 'measure', (1,), (1,))]
print(dag.edges)
# ((0, 1), (1, 2), (1, 3))
```

To parse an existing circuit file, pass its path:

```python
from nadit_physical_compiler import load_qasm

dag_from_file = load_qasm("circuit.qasm")
```

- `loads_qasm(source: str) -> PhysicalDAG` parses an in-memory circuit.
- `load_qasm(path) -> PhysicalDAG` parses a circuit file.

Both entry points return NADiT records; downstream callers do not need Qiskit
objects.

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
unbound parameters are outside this profile. This is not a complete OpenQASM
3.0 implementation.

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

Each node has `id`, `name`, `qubits`, `clbits`, `params`, and an optional
`condition`. Node IDs are consecutive from zero after custom-gate expansion and
branch flattening. Qubit and classical-bit indices refer to their respective
flattened wire spaces. Parameters are numeric; gate angles use the parser's
radian convention.

`ResultRef(measurement_id)` identifies one measurement occurrence.
`Condition(result, value)` requires that result to equal `0` or `1`; the default
value is `1`. The importer binds each condition to the latest preceding
measurement of its source bit. A later write to that bit does not change an
existing reference.

Qiskit supplies wire dependencies, including measurement write ordering and
barriers. The adapter adds an edge from the producing measurement to each
conditioned operation. Both `if` and `else` bodies become ordinary nodes with
opposite conditions; branch-local global phase becomes a conditioned
`global_phase` node. Unconditional global phase remains on the DAG.

Nodes on disjoint wires can remain independent. This dependency graph does not
claim that independent operations are simultaneously executable on a device.

## Measurement feedback example

The following example forces the measured bit to `1`, executes the `x` branch,
and skips the `z` branch:

```python
from nadit_physical_compiler import FakeBackend, ResultRef, execute, loads_qasm

dag = loads_qasm("""
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
""")

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
returns an `ExecutionTrace`. Operations start when their dependencies are
complete and any required result is available. A false condition produces a
zero-duration `skipped` event and releases successors. In this example, the
independent `h` runs while measurement feedback is pending.

Times use abstract units: operations default to one unit, while `barrier` and
`global_phase` default to zero. The `durations` mapping can override times by
operation name. `measurement_latency` is the delay from measurement completion
to result availability; it does not extend the measurement operation itself.

`trace.operations` is ordered by operation ID; `trace.measurements` is ordered
by result availability. `trace.duration` includes the final operation completion
or result delivery, whichever is later.

`FakeBackend` combines its seed with each measurement ID, so results do not
depend on execution order. `overrides` maps particular `ResultRef` values to
explicit bits. A custom backend can provide `measure(result: ResultRef) -> int`.
This runner exercises feedback semantics; its timing and bits are not a device
schedule or quantum-state simulation.

## Design records

The supplied circuit's exhaustive feedback study and reproduction commands
are recorded in [Feedback validation](docs/feedback-validation.md).

The original static frontend boundary and dependency choice are recorded in
[ADR 0003](../../docs/decisions/0003-qasm-physical-dag-frontend.md).
[ADR 0004](../../docs/decisions/0004-measurement-feedback.md) extends that
boundary with measurement-result references, fixed conditional bodies, and
fake feedback execution.
The complete compiler direction is recorded in
[ADR 0002](../../docs/decisions/0002-pluggable-physical-compiler.md).

The current discussion covers configurable AOD motion constraints through a
constraint map, including fixed-grid translation and endpoint row/column
order preservation; configurable zoning; static SLM lattice sites and timed
SLM/AOD transfers; and simple move-from/to actions timed by a distance function
without velocity dynamics. These describe the user's direction, not selected
APIs or algorithms.

Project-wide decisions belong in the root `docs/decisions/`; module-specific
design notes belong in this module's `docs/`.

The user leads the module design step by step in the NADiT physical compiler
chat. Python packaging and the QASM frontend interface are selected for this
increment; device interfaces and subsequent compilation strategies remain open.

Module design record: [Atom IR and reusable components](docs/atom-ir-and-components.md).
It separates confirmed requirements, proposed interfaces, and open details.
