"""Tests for the lightweight physical dependency representation."""

from math import inf, nan

import pytest

from nadit_physical_compiler import PhysicalDAG, PhysicalOperation, Register


def test_minimal_dag_keeps_operation_and_register_information() -> None:
    nodes = (
        PhysicalOperation(0, "h", (0,), (), ()),
        PhysicalOperation(1, "measure", (0,), (1,), ()),
    )
    dag = PhysicalDAG(
        num_qubits=2,
        num_clbits=2,
        nodes=nodes,
        edges=((0, 1),),
        quantum_registers=(Register("q", (0, 1)),),
        classical_registers=(Register("c", (0, 1)),),
        global_phase=0.25,
    )

    assert dag.nodes == nodes
    assert dag.edges == ((0, 1),)
    assert dag.quantum_registers[0].bits == (0, 1)
    assert dag.classical_registers[0].name == "c"
    assert dag.global_phase == 0.25


def test_empty_dag_is_valid() -> None:
    dag = PhysicalDAG(num_qubits=0, num_clbits=0, nodes=(), edges=())

    assert dag.nodes == ()
    assert dag.quantum_registers == ()
    assert dag.classical_registers == ()
    assert dag.global_phase == 0.0


@pytest.mark.parametrize(("num_qubits", "num_clbits"), [(-1, 0), (0, -1)])
def test_negative_wire_counts_are_rejected(num_qubits: int, num_clbits: int) -> None:
    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(num_qubits=num_qubits, num_clbits=num_clbits, nodes=(), edges=())


@pytest.mark.parametrize("ids", [(1,), (0, 0), (0, 2), (-1,)])
def test_node_ids_must_be_contiguous_and_match_tuple_order(
    ids: tuple[int, ...],
) -> None:
    nodes = tuple(PhysicalOperation(index, "x", (0,), (), ()) for index in ids)

    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(num_qubits=1, num_clbits=0, nodes=nodes, edges=())


@pytest.mark.parametrize(
    ("qubits", "clbits"),
    [((-1,), ()), ((1,), ()), ((0,), (-1,)), ((0,), (1,))],
)
def test_operation_wire_indices_must_be_in_range(
    qubits: tuple[int, ...], clbits: tuple[int, ...]
) -> None:
    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(
            num_qubits=1,
            num_clbits=1,
            nodes=(PhysicalOperation(0, "measure", qubits, clbits, ()),),
            edges=(),
        )


@pytest.mark.parametrize("edge", [(-1, 0), (0, 2), (1, 0), (0, 0)])
def test_dependency_edges_must_reference_forward_existing_nodes(
    edge: tuple[int, int],
) -> None:
    nodes = (
        PhysicalOperation(0, "h", (0,), (), ()),
        PhysicalOperation(1, "z", (0,), (), ()),
    )

    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(num_qubits=1, num_clbits=0, nodes=nodes, edges=(edge,))


@pytest.mark.parametrize("phase", [inf, -inf, nan])
def test_global_phase_must_be_finite(phase: float) -> None:
    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(num_qubits=0, num_clbits=0, nodes=(), edges=(), global_phase=phase)


@pytest.mark.parametrize(
    "edges",
    [
        ((0, 1), (0, 1)),
        ((0, 2), (0, 1)),
    ],
)
def test_dependency_edges_must_be_unique_and_sorted(
    edges: tuple[tuple[int, int], ...],
) -> None:
    nodes = tuple(PhysicalOperation(index, "x", (0,), (), ()) for index in range(3))

    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(num_qubits=1, num_clbits=0, nodes=nodes, edges=edges)


@pytest.mark.parametrize("quantum", [True, False])
@pytest.mark.parametrize("bits", [(-1,), (2,)])
def test_register_wire_indices_must_be_in_range(
    quantum: bool, bits: tuple[int, ...]
) -> None:
    registers = (Register("register", bits),)

    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(
            num_qubits=2,
            num_clbits=2,
            nodes=(),
            edges=(),
            quantum_registers=registers if quantum else (),
            classical_registers=() if quantum else registers,
        )


@pytest.mark.parametrize("quantum", [True, False])
def test_register_names_must_be_unique(quantum: bool) -> None:
    registers = (Register("repeated", (0,)), Register("repeated", (1,)))

    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(
            num_qubits=2,
            num_clbits=2,
            nodes=(),
            edges=(),
            quantum_registers=registers if quantum else (),
            classical_registers=() if quantum else registers,
        )


@pytest.mark.parametrize("parameter", [inf, -inf, nan])
def test_operation_parameters_must_be_finite(parameter: float) -> None:
    with pytest.raises(ValueError, match=r".+"):
        PhysicalDAG(
            num_qubits=1,
            num_clbits=0,
            nodes=(PhysicalOperation(0, "rx", (0,), (), (parameter,)),),
            edges=(),
        )
