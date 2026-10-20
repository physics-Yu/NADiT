"""Immutable, device-independent physical circuit dependency data."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class PhysicalOperation:
    """An operation on circuit wires, before atom or device binding.

    Args:
        id: Zero-based position in the normalized circuit.
        name: Standard operation name, such as ``h``, ``cx``, or ``measure``.
        qubits: Ordered indices into the circuit's quantum wires.
        clbits: Ordered indices into the circuit's classical wires.
        params: Bound numeric operation parameters; angles are in radians.

    Wire indices and identifiers are checked by the containing PhysicalDAG.
    """

    id: int
    name: str
    qubits: tuple[int, ...]
    clbits: tuple[int, ...]
    params: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Register:
    """A named source register mapped to flat circuit wire indices.

    Args:
        name: Register name retained by the QASM importer.
        bits: Flat indices, in register order, including unused wires.
    """

    name: str
    bits: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PhysicalDAG:
    """A circuit's operations and direct wire dependencies.

    Args:
        num_qubits: Total number of quantum wires, including unused wires.
        num_clbits: Total number of classical wires, including unused wires.
        nodes: Operations in normalized source order, with consecutive IDs.
        edges: Sorted, unique ``(predecessor, successor)`` operation IDs.
        quantum_registers: Named quantum registers and their flat indices.
        classical_registers: Named classical registers and their flat indices.
        global_phase: Circuit global phase in radians.

    Raises:
        ValueError: If counts, identifiers, indices, edges, or numeric data
            violate the representation's invariants.

    Forward-only edges establish acyclicity. A missing edge is not a claim
    that operations can run simultaneously on a physical device.
    """

    num_qubits: int
    num_clbits: int
    nodes: tuple[PhysicalOperation, ...]
    edges: tuple[tuple[int, int], ...]
    quantum_registers: tuple[Register, ...] = ()
    classical_registers: tuple[Register, ...] = ()
    global_phase: float = 0.0

    def __post_init__(self) -> None:
        if self.num_qubits < 0 or self.num_clbits < 0:
            raise ValueError("Circuit wire counts must be nonnegative.")
        if not isfinite(self.global_phase):
            raise ValueError("Circuit global phase must be finite.")
        for index, node in enumerate(self.nodes):
            if node.id != index:
                raise ValueError(f"Expected operation ID {index}, got {node.id}.")
            if not node.name:
                raise ValueError(f"Operation {index} has no name.")
            _check_indices(node.qubits, self.num_qubits, f"Operation {index} qubits")
            _check_indices(node.clbits, self.num_clbits, f"Operation {index} clbits")
            if not all(isfinite(value) for value in node.params):
                raise ValueError(f"Operation {index} parameters must be finite.")
        _check_registers(self.quantum_registers, self.num_qubits, "Quantum")
        _check_registers(self.classical_registers, self.num_clbits, "Classical")
        if self.edges != tuple(sorted(set(self.edges))):
            raise ValueError("Dependency edges must be sorted and unique.")
        for source, target in self.edges:
            if not 0 <= source < target < len(self.nodes):
                raise ValueError(f"Invalid forward dependency edge {(source, target)}.")


def _check_indices(indices: tuple[int, ...], count: int, context: str) -> None:
    if any(index < 0 or index >= count for index in indices):
        raise ValueError(f"{context} contain an index outside [0, {count}).")


def _check_registers(registers: tuple[Register, ...], count: int, context: str) -> None:
    names = [register.name for register in registers]
    if len(set(names)) != len(names):
        raise ValueError(f"{context} register names must be unique.")
    for register in registers:
        if not register.name:
            raise ValueError(f"{context} register has no name.")
        _check_indices(register.bits, count, f"{context} register {register.name!r}")
