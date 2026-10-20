# 0003: Static QASM Frontend and Physical DAG

Status: Accepted on 2026-10-20.

The measurement-feedback extension is recorded in
[ADR 0004](0004-measurement-feedback.md).

## Context

The physical compiler needs a standard circuit input and an inspectable
dependency graph before device-dependent compilation is implemented. The user
approved static OpenQASM 2.0 and 3.0 support, a module-local Python package, and
the proposed Qiskit-based parsing and conversion flow.

This increment implements the input boundary of
[ADR 0002](0002-pluggable-physical-compiler.md).

## Decision

Use Python 3.12 in `modules/physical_compiler/`, with `pyproject.toml`, `uv.lock`,
and the package `src/nadit_physical_compiler/`.

Use Qiskit 2.6.0 for circuit parsing and standard DAG conversion, with
`qiskit-qasm3-import` 0.6.0 for OpenQASM 3.0 input. These dependencies avoid a
project-specific QASM parser and dependency-construction algorithm.

The frontend follows:

```text
QASM source
    -> parsed Qiskit circuit
    -> static-profile validation and custom-gate expansion
    -> Qiskit circuit_to_dag
    -> NADiT PhysicalDAG
```

Expose two loading functions:

- `loads_qasm(source: str) -> PhysicalDAG`
- `load_qasm(path) -> PhysicalDAG`

Keep parsing, profile validation, and adaptation in `qasm.py`. Keep the
device-independent records in `ir.py` and export the public interface through
`__init__.py`.

The Physical DAG contains:

- Operation nodes: `id`, `name`, `qubits`, `clbits`, and numeric `params`.
- Directed wire-dependency edges.
- Quantum and classical wire counts and register metadata.
- The circuit global phase.

A register record contains its `name` and flattened `bits`. Nodes are numbered
consecutively in normalized circuit order. Qiskit objects do not appear in the
public records.

Support static standard gates with numeric parameters, measurement, reset, and
barrier. Expand custom gate definitions from the same source file before
building the DAG. Reject dynamic conditions and loops, opaque operations,
unbound parameters, and other unsupported constructs explicitly. OpenQASM 3.0
support is limited to this profile and the importer's supported syntax.

For both text and file inputs, accept only `qelib1.inc` for OpenQASM 2.0 and
`stdgates.inc` for OpenQASM 3.0. Reject arbitrary external include files and
paths. The user accepted this first-version boundary; it avoids the included
expression parsing issue reported in
[Qiskit issue 16918](https://github.com/Qiskit/qiskit/issues/16918).
Custom gate definitions in the circuit source remain supported.

Use pytest for functional tests, Ruff for formatting and linting, and mypy for
type checking. Keep runtime and development dependencies in the module-local
environment and lockfile.

## Alternatives

- A custom QASM parser and DAG builder would duplicate standard functionality
  and enlarge the initial maintenance scope.
- Exposing Qiskit's DAG directly would couple later compiler strategies to
  parser-library objects.
- Compiling into a native device basis at import time would couple input
  parsing to device capabilities and prematurely select a compilation strategy.

## Consequences

Downstream compilation receives explicit operations and dependencies in
independent records. Standard gates retain their semantics. Optimization,
native-gate lowering, placement, transport planning, and scheduling belong
to the physical compiler.

Wire dependencies preserve original circuit order. Lack of a dependency does
not imply that two operations can execute concurrently on a device.

Unsupported input fails at the frontend boundary. Dynamic circuits, additional
QASM language features, device interfaces, and executable Atom IR remain
separate increments requiring their own agreed scope.
