"""Original static layer ordering and architecture capacity semantics."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.device import Position, SLMSite
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement.geometry import PlacementGeometry, SLMArray
from nadit_physical_compiler.scheduling import SourceASAPScheduler, source_capacity


def _geometry(*sizes: tuple[int, int]) -> PlacementGeometry:
    arrays = []
    for index, (rows, columns) in enumerate(sizes):
        sites = tuple(
            tuple(
                SLMSite(f"{index}:{row}:{column}", Position(10 * column, 10 * row))
                for column in range(columns)
            )
            for row in range(rows)
        )
        left = SLMArray(str(index), "left", sites, Position(10, 10), sites)
        arrays.append((left, replace(left, kind="right")))
    return PlacementGeometry((), tuple(arrays))


def _dag(
    *operations: tuple[str, tuple[int, ...]],
    num_qubits: int = 6,
) -> PhysicalDAG:
    nodes = tuple(
        PhysicalOperation(index, name, qubits, (), (0.1,) if name == "rz" else ())
        for index, (name, qubits) in enumerate(operations)
    )
    previous: dict[int, int] = {}
    edges = set()
    for node in nodes:
        for qubit in node.qubits:
            if qubit in previous:
                edges.add((previous[qubit], node.id))
            previous[qubit] = node.id
    return PhysicalDAG(num_qubits, 0, nodes, tuple(sorted(edges)))


@pytest.mark.parametrize(("factor", "expected"), [(0, 2), (0.2, 3), (0.9, 11), (1, 13)])
def test_capacity_rounds_each_zone_separately(factor: float, expected: int) -> None:
    assert source_capacity(_geometry((1, 3), (2, 5)), factor) == expected


@pytest.mark.parametrize("factor", [-0.1, 1.1, float("nan"), float("inf")])
def test_capacity_rejects_factors_outside_source_domain(factor: float) -> None:
    with pytest.raises(ValueError, match="max_filling_factor"):
        source_capacity(_geometry((1, 2)), factor)


def test_capacity_requires_a_gate_zone() -> None:
    with pytest.raises(ValueError, match="entanglement zone"):
        source_capacity(_geometry())


def test_static_single_layers_follow_wire_cz_depth_and_source_input_order() -> None:
    dag = _dag(
        ("cz", (0, 1)),
        ("rz", (0,)),
        ("rz", (4,)),
        ("cz", (2, 3)),
        ("rz", (2,)),
        ("cz", (0, 2)),
    )
    scheduler = SourceASAPScheduler(dag, _geometry((1, 3)))
    assert [layer.operation_ids for layer in scheduler.single_qubit_layers] == [
        (2,),
        (1, 4),
        (),
    ]
    assert [layer.operation_ids for layer in scheduler.two_qubit_layers] == [
        (0, 3),
        (5,),
    ]
    assert scheduler.select(dag, (0, 2, 3)).operation_ids == (2,)
    assert scheduler.select(dag, (0, 3)).operation_ids == (0, 3)
    assert scheduler.select(dag, (1, 4)).operation_ids == (1,)
    assert scheduler.select(dag, (0, 3)).operation_ids == (0, 3)


def test_capacity_moves_disjoint_gates_to_the_next_available_phase() -> None:
    dag = _dag(("cz", (0, 1)), ("cz", (2, 3)), ("cz", (4, 5)))
    scheduler = SourceASAPScheduler(dag, _geometry((1, 2)), 0.5)
    assert [layer.operation_ids for layer in scheduler.two_qubit_layers] == [
        (0,),
        (1,),
        (2,),
    ]


@pytest.mark.parametrize("name", ["global_ry", "barrier"])
def test_global_operations_synchronize_following_cz_layers(name: str) -> None:
    dag = _dag(("cz", (0, 1)), (name, tuple(range(6))), ("cz", (2, 3)))
    scheduler = SourceASAPScheduler(dag, _geometry((1, 4)))
    assert [layer.operation_ids for layer in scheduler.two_qubit_layers] == [
        (0,),
        (2,),
    ]
    assert scheduler.single_qubit_layers[1].operation_ids == (
        (1,) if name == "global_ry" else ()
    )


def test_empty_static_circuit_has_no_layers() -> None:
    dag = _dag()
    scheduler = SourceASAPScheduler(dag, _geometry((1, 2)))
    assert scheduler.single_qubit_layers == scheduler.two_qubit_layers == ()
    assert scheduler.select(dag, ()).operation_ids == ()


def test_local_barrier_is_not_source_static_semantics() -> None:
    with pytest.raises(ValueError, match="global barriers"):
        SourceASAPScheduler(_dag(("barrier", (0,))), _geometry((1, 2)))


@pytest.mark.parametrize("name", ["measure", "reset", "h"])
def test_dynamic_and_unprepared_operations_use_the_online_path(name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported static"):
        SourceASAPScheduler(_dag((name, (0,))), _geometry((1, 2)))
