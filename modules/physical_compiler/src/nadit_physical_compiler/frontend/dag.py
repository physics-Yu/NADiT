"""Construct physical dependencies after native gate translation."""

from qiskit.converters import circuit_to_dag
from qiskit.dagcircuit import DAGOpNode

from ..physical_ir import PhysicalDAG, PhysicalOperation, Register
from .native import NativeCircuit


def build_physical_dag(native: NativeCircuit) -> PhysicalDAG:
    """Build a native DAG with wire dependencies and source measurement reads.

    Args:
        native: Native circuit and source metadata from frontend translation.

    Returns:
        Physical operations with consecutive node IDs and stable source IDs.

    Raises:
        ValueError: If the native instructions violate physical IR invariants.
    """
    circuit = native.circuit
    dag = circuit_to_dag(circuit)
    # The pinned converter retains circuit insertion order in op_nodes().
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
            condition=native.conditions.get(node_ids[node]),
            source_id=native.source_ids[node_ids[node]],
        )
        for node in operations
    )
    producers = {node.result: node.id for node in nodes if node.name == "measure"}
    edges = {
        (node_ids[predecessor], node_ids[node])
        for node in operations
        for predecessor in dag.predecessors(node)
        if isinstance(predecessor, DAGOpNode)
    }
    edges.update(
        (producers[condition.result], index)
        for index, condition in native.conditions.items()
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
