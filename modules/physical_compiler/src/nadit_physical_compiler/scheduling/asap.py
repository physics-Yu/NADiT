"""Deterministic ready-layer selection with configurable CZ capacity."""

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG


class ReadyScheduler:
    """Select homogeneous layers without owning dependency progress.

    Args:
        max_cz_gates: Maximum number of disjoint CZ gates in one layer.

    Raises:
        ValueError: If CZ capacity is not positive.
    """

    def __init__(self, max_cz_gates: int = 1) -> None:
        if max_cz_gates <= 0:
            raise ValueError("CZ layer capacity must be positive.")
        self.max_cz_gates = max_cz_gates

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Drain ready local rotations before selecting a CZ layer.

        Args:
            dag: Native physical operations and dependencies.
            ready: Node IDs with resolved dependencies and true conditions.

        Returns:
            Disjoint operations sharing the selected operation's name.
            CZ layers respect the configured capacity; readout remains singular.
        """
        if not ready:
            return GateLayer(())
        ordered = sorted(ready)
        first = ordered[0]
        if dag.nodes[first].name == "cz":
            first = next(
                (
                    index
                    for index in ordered
                    if dag.nodes[index].name in ("rx", "ry", "rz")
                ),
                first,
            )
        name = dag.nodes[first].name
        if name not in ("rx", "ry", "rz", "reset", "cz"):
            return GateLayer((ordered[0],))
        capacity = self.max_cz_gates if name == "cz" else len(ordered)
        selected: list[int] = []
        occupied: set[int] = set()
        for operation_id in ordered:
            operation = dag.nodes[operation_id]
            if operation.name == name and occupied.isdisjoint(operation.qubits):
                selected.append(operation_id)
                occupied.update(operation.qubits)
                if len(selected) == capacity:
                    break
        return GateLayer(tuple(selected))
