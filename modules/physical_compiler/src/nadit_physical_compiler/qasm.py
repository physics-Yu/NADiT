"""OpenQASM import with single-bit measurement feedback and wire dependencies."""

import re
from pathlib import Path

from openqasm3.parser import QASM3ParsingError
from qiskit import QuantumCircuit, qasm2, qasm3
from qiskit.circuit import (
    Barrier,
    ClassicalRegister,
    Clbit,
    Gate,
    IfElseOp,
    Measure,
    Reset,
)
from qiskit.circuit.library import GlobalPhaseGate, get_standard_gate_name_mapping
from qiskit.converters import circuit_to_dag
from qiskit.dagcircuit import DAGOpNode
from qiskit.exceptions import QiskitError

from .physical_ir import Condition, PhysicalDAG, PhysicalOperation, Register, ResultRef

# Detect only the version header. Qiskit parses and validates the language.
_VERSION_HEADER = re.compile(
    r"\A(?:\s|//[^\n]*(?:\n|\Z)|/\*.*?\*/)*"
    r"OPENQASM\s+([0-9]+(?:\.[0-9]+)?)\s*;",
    re.DOTALL,
)
# Scan include targets without interpreting circuit syntax or comment contents.
_INCLUDE_TOKENS = re.compile(
    r"""//[^\r\n]*|/\*.*?\*/|"[^"\r\n]*"|'[^'\r\n]*'|(?P<include>\binclude\b)""",
    re.DOTALL,
)
_STANDARD_GATE_CLASSES = {
    gate.base_class: gate.name
    for gate in get_standard_gate_name_mapping().values()
    if isinstance(gate, Gate)
}
_CIRCUIT_INSTRUCTIONS = (Measure, Reset, Barrier)


def loads_qasm(source: str) -> PhysicalDAG:
    """Parse OpenQASM 2.0 or 3.0 text into a physical dependency graph.

    Args:
        source: QASM text with an explicit version header. Only the version's
            standard gate include is supported. Single-bit conditions may
            read previously measured bits.

    Returns:
        A device-independent DAG preserving circuit wire dependencies.

    Raises:
        ValueError: If the input is malformed or contains unsupported semantics.
    """
    return _import_qasm(source, "<string>")


def load_qasm(path: str | Path) -> PhysicalDAG:
    """Read a UTF-8 QASM file and construct its physical dependency graph.

    Args:
        path: Source file. Include support is identical to loads_qasm.

    Returns:
        The same representation as loads_qasm.

    Raises:
        OSError: If the source file cannot be read.
        UnicodeError: If the source file is not valid UTF-8.
        ValueError: If parsing or conversion fails, including the source path.
    """
    source_path = Path(path)
    source = source_path.read_text(encoding="utf-8-sig")
    return _import_qasm(source, str(source_path))


def _import_qasm(source: str, source_name: str) -> PhysicalDAG:
    source = source.removeprefix("\ufeff")
    match = _VERSION_HEADER.match(source)
    if match is None:
        raise ValueError(f"{source_name}: missing or invalid OPENQASM version header.")
    version = match.group(1)
    if version not in ("2.0", "3", "3.0"):
        raise ValueError(f"{source_name}: unsupported OpenQASM version {version!r}.")
    try:
        _validate_includes(source, "qelib1.inc" if version == "2.0" else "stdgates.inc")
        if version == "2.0":
            circuit = qasm2.loads(source, include_path=(), strict=True)
        else:
            circuit = qasm3.loads(source)
    except (
        QiskitError,
        QASM3ParsingError,
        ValueError,
        IndexError,
        TypeError,
        RecursionError,
        ArithmeticError,
    ) as error:
        detail = str(error) or str(error.__cause__) or "invalid syntax"
        raise ValueError(
            f"{source_name}: QASM import failed ({type(error).__name__}): {detail}"
        ) from error
    try:
        return _to_physical_dag(_normalize(circuit))
    except (QiskitError, ValueError, RecursionError) as error:
        raise ValueError(f"{source_name}: unsupported circuit: {error}") from error


def _validate_includes(source: str, standard_library: str) -> None:
    tokens = iter(_INCLUDE_TOKENS.finditer(source))
    for token in tokens:
        if token.lastgroup != "include":
            continue
        for target in tokens:
            value = target.group()
            if value.startswith(("//", "/*")):
                continue
            if value not in (f'"{standard_library}"', f"'{standard_library}'"):
                raise ValueError(
                    f"Only the standard include {standard_library!r} is supported; "
                    f"found {value!r}."
                )
            break


def _normalize(
    circuit: QuantumCircuit, *, in_condition: bool = False
) -> QuantumCircuit:
    """Expand defined gates while retaining standard operations and source order."""
    if circuit.parameters:
        raise ValueError(
            "Unbound input parameters are outside the static QASM profile."
        )
    if circuit.layout is not None:
        raise ValueError(
            "Hardware-qubit addressing is outside the circuit-wire profile."
        )
    normalized = circuit.copy_empty_like()
    for instruction in circuit.data:
        operation = instruction.operation
        if isinstance(operation, IfElseOp):
            if in_condition:
                raise ValueError("Nested conditions are not supported.")
            blocks = tuple(
                _normalize(block, in_condition=True) for block in operation.blocks
            )
            normalized.append(
                operation.replace_blocks(blocks),
                instruction.qubits,
                instruction.clbits,
            )
            continue
        if in_condition and isinstance(operation, Measure):
            raise ValueError("Measurement inside a condition is not supported.")
        standard_name = _STANDARD_GATE_CLASSES.get(operation.base_class)
        if isinstance(operation, _CIRCUIT_INSTRUCTIONS) or (
            standard_name is not None and operation.name == standard_name
        ):
            normalized.append(operation, instruction.qubits, instruction.clbits)
            continue
        if not isinstance(operation, Gate):
            raise ValueError(
                f"Unsupported circuit operation {operation.name!r}; "
                "expected a gate, measurement, reset, or barrier."
            )
        definition = operation.definition
        if definition is None:
            raise ValueError(f"Opaque gate {operation.name!r} has no definition.")
        normalized.compose(
            _normalize(definition, in_condition=in_condition),
            qubits=instruction.qubits,
            clbits=instruction.clbits,
            inplace=True,
        )
    return normalized


def _flatten_conditions(
    circuit: QuantumCircuit,
) -> tuple[QuantumCircuit, dict[int, Condition]]:
    """Bind each classical read to its producer and flatten fixed branch bodies."""
    flattened = circuit.copy_empty_like()
    conditions: dict[int, Condition] = {}
    latest_results: dict[Clbit, ResultRef] = {}
    for instruction in circuit.data:
        operation = instruction.operation
        if not isinstance(operation, IfElseOp):
            index = len(flattened.data)
            flattened.append(operation, instruction.qubits, instruction.clbits)
            if isinstance(operation, Measure):
                latest_results[instruction.clbits[0]] = ResultRef(index)
            continue
        result, value = _read_condition(operation, latest_results)
        for branch, block in enumerate(operation.blocks):
            condition = Condition(result, value if branch == 0 else 1 - value)
            qubit_map = dict(zip(block.qubits, instruction.qubits, strict=True))
            if block.global_phase:
                conditions[len(flattened.data)] = condition
                flattened.append(GlobalPhaseGate(float(block.global_phase)), [])
            for item in block.data:
                conditions[len(flattened.data)] = condition
                flattened.append(
                    item.operation, [qubit_map[bit] for bit in item.qubits]
                )
    return flattened, conditions


def _read_condition(
    operation: IfElseOp, latest_results: dict[Clbit, ResultRef]
) -> tuple[ResultRef, int]:
    raw = operation.condition
    if not isinstance(raw, tuple):
        raise ValueError("Only single-bit equality conditions are supported.")
    bit, value = raw
    if isinstance(bit, ClassicalRegister) and len(bit) == 1:
        bit = bit[0]
    if not isinstance(bit, Clbit) or value not in (0, 1):
        raise ValueError("Only single-bit equality conditions are supported.")
    if bit not in latest_results:
        raise ValueError("A condition must read a previously measured bit.")
    return latest_results[bit], int(value)


def _to_physical_dag(circuit: QuantumCircuit) -> PhysicalDAG:
    circuit, conditions = _flatten_conditions(circuit)
    dag = circuit_to_dag(circuit)
    # The pinned converter inserts op_nodes in circuit order, not a reordered
    # topological traversal. Tests protect stable IDs on independent wires.
    operations = dag.op_nodes()
    node_ids = {node: index for index, node in enumerate(operations)}
    qubits = {bit: index for index, bit in enumerate(dag.qubits)}
    clbits = {bit: index for index, bit in enumerate(dag.clbits)}
    nodes = tuple(
        PhysicalOperation(
            id=node_ids[node],
            name=node.name,
            qubits=tuple(qubits[bit] for bit in node.qargs),
            clbits=tuple(clbits[bit] for bit in node.cargs),
            params=tuple(float(value) for value in node.op.params),
            condition=conditions.get(node_ids[node]),
        )
        for node in operations
    )
    edges = {
        (node_ids[predecessor], node_ids[node])
        for node in operations
        for predecessor in dag.predecessors(node)
        if isinstance(predecessor, DAGOpNode)
    }
    edges.update(
        (condition.result.measurement_id, index)
        for index, condition in conditions.items()
    )
    return PhysicalDAG(
        num_qubits=len(qubits),
        num_clbits=len(clbits),
        nodes=nodes,
        edges=tuple(sorted(edges)),
        quantum_registers=tuple(
            Register(register.name, tuple(qubits[bit] for bit in register))
            for register in circuit.qregs
        ),
        classical_registers=tuple(
            Register(register.name, tuple(clbits[bit] for bit in register))
            for register in circuit.cregs
        ),
        global_phase=float(circuit.global_phase),
    )
