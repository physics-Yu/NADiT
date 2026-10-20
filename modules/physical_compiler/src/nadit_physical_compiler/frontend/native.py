"""Native gate translation with source identities fixed before expansion."""

from dataclasses import dataclass

from qiskit import QuantumCircuit
from qiskit.circuit import Gate, Instruction
from qiskit.circuit.equivalence_library import SessionEquivalenceLibrary
from qiskit.circuit.library import GlobalPhaseGate
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import BasisTranslator

from ..physical_ir import Condition


@dataclass(frozen=True, slots=True)
class GateSet:
    """Target standard gate names; angles and arities follow Qiskit conventions."""

    names: tuple[str, ...] = ("rx", "ry", "rz", "cz")


DEFAULT_GATE_SET = GateSet()


@dataclass(frozen=True, slots=True)
class NativeCircuit:
    """Native circuit plus source identities and guards for each instruction."""

    circuit: QuantumCircuit
    source_ids: tuple[int, ...]
    conditions: dict[int, Condition]


def to_native(
    circuit: QuantumCircuit,
    conditions: dict[int, Condition],
    gate_set: GateSet,
) -> NativeCircuit:
    """Translate source gates before graph construction, retaining branch phases.

    Args:
        circuit: Normalized circuit with fixed conditional bodies flattened.
        conditions: Guards keyed by instruction index before native expansion.
        gate_set: Standard gate names accepted by the target.

    Returns:
        Native instructions, their source indices, and their inherited guards.

    Raises:
        QiskitError: If the chosen gate set cannot represent a source operation.
    """
    output = circuit.copy_empty_like()
    source_ids: list[int] = []
    guards: dict[int, Condition] = {}
    translator = PassManager(
        BasisTranslator(SessionEquivalenceLibrary, list(gate_set.names))
    )
    cache: dict[tuple[str, int, tuple[float, ...]], QuantumCircuit] = {}

    def append(operation: Instruction, qubits: tuple[int, ...]) -> None:
        index = len(output.data)
        output.append(
            operation,
            [item.qubits[bit] for bit in qubits],
            item.clbits if not isinstance(operation, Gate) else (),
        )
        source_ids.append(source_id)
        if condition is not None:
            guards[index] = condition

    for source_id, item in enumerate(circuit.data):
        operation = item.operation
        condition = conditions.get(source_id)
        if (
            not isinstance(operation, Gate)
            or isinstance(operation, GlobalPhaseGate)
            or operation.name in gate_set.names
        ):
            append(operation, tuple(range(len(item.qubits))))
            continue
        key = (
            operation.name,
            operation.num_qubits,
            tuple(float(value) for value in operation.params),
        )
        if key not in cache:
            source = QuantumCircuit(operation.num_qubits)
            source.append(operation, range(operation.num_qubits))
            cache[key] = translator.run(source)
        translated = cache[key]
        if translated.global_phase:
            if condition is None:
                output.global_phase += translated.global_phase
            else:
                append(GlobalPhaseGate(float(translated.global_phase)), ())
        for child in translated.data:
            append(
                child.operation,
                tuple(translated.find_bit(bit).index for bit in child.qubits),
            )
    return NativeCircuit(output, tuple(source_ids), guards)
