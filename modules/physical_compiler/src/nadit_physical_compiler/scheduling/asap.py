"""Deterministic ready-layer selection with configurable CZ capacity."""

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG


class ReadyScheduler:
    """Select homogeneous layers without owning dependency progress.

    Args:
        max_cz_gates: Maximum number of disjoint CZ gates in one layer.
        max_measurements: Ready measurements per layer; None imposes no count limit.

    Raises:
        ValueError: If CZ capacity is not positive.
    """

    def __init__(
        self, max_cz_gates: int = 1, *, max_measurements: int | None = 1
    ) -> None:
        if max_cz_gates <= 0:
            raise ValueError("CZ layer capacity must be positive.")
        self.max_cz_gates = max_cz_gates
        if max_measurements is not None and max_measurements < 1:
            raise ValueError("Measurement capacity must be positive or None.")
        self.max_measurements = max_measurements

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Drain ready local rotations before selecting a CZ layer.

        Args:
            dag: Native physical operations and dependencies.
            ready: Node IDs with resolved dependencies and true conditions.

        Returns:
            Disjoint operations sharing the selected operation's name.
            CZ and measurement layers respect their configured count capacities.
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
                    if dag.nodes[index].name in ("rx", "ry", "rz", "u", "global_ry")
                ),
                first,
            )
        name = dag.nodes[first].name
        if name not in ("rx", "ry", "rz", "u", "reset", "cz", "measure"):
            return GateLayer((ordered[0],))
        capacity = self.max_cz_gates if name == "cz" else len(ordered)
        if name == "measure" and self.max_measurements is not None:
            capacity = self.max_measurements
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
