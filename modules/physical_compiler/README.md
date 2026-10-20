# Physical Compiler

This module provides a static OpenQASM 2.0 / 3.0 frontend that produces a
device-independent Physical DAG. It parses the circuit with Qiskit, expands
custom gate definitions, and adapts the standard circuit DAG into a small NADiT
data model.

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
│   ├── qasm.py   # QASM parsing, scope checks, and DAG adaptation
│   ├── ir.py     # Physical DAG records
│   └── py.typed  # Package typing marker
├── tests/        # Frontend and IR tests
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

The supported profile is static circuits with standard gates, numeric
parameters, measurement, reset, and barrier. Custom gate definitions in the
same source file are expanded before DAG construction. Numeric expressions
such as `pi / 2` are resolved by the parser.

Dynamic conditions and control-flow loops, opaque operations, and unbound
parameters are rejected. This profile is not a complete OpenQASM 3.0
implementation. Parser errors and unsupported constructs are reported rather
than silently dropped.

Both text and file inputs accept only the standard library include:
`qelib1.inc` for OpenQASM 2.0 or `stdgates.inc` for OpenQASM 3.0. Arbitrary
external include files and paths are rejected; place custom gate definitions
in the circuit source itself.

## Physical DAG contract

| Field | Meaning |
| --- | --- |
| `num_qubits`, `num_clbits` | Number of flattened quantum and classical wires |
| `nodes` | Operation records in normalized circuit order |
| `edges` | Directed `(predecessor_id, successor_id)` wire dependencies |
| `quantum_registers`, `classical_registers` | Register records with `name` and flattened `bits` |
| `global_phase` | Circuit global phase in radians |

Each node has `id`, `name`, `qubits`, `clbits`, and `params`. Node IDs are
consecutive from zero after custom-gate expansion. Qubit and classical-bit
indices refer to their respective flattened wire spaces. Parameters are numeric;
gate angles use the parser's radian convention.

Edges preserve operation order on each quantum or classical wire, including
measurement writes and barriers on their specified wires. Nodes on disjoint
wires can remain independent. This dependency graph does not claim that
independent operations are simultaneously executable on a device.

## Design records

The accepted frontend boundary and dependency choice are recorded in
[ADR 0003](../../docs/decisions/0003-qasm-physical-dag-frontend.md).
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
