"""Deterministic ready-layer selection for the one-pair compiler."""

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG


class ReadyScheduler:
    """Select homogeneous layers without owning dependency progress."""

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Select the earliest ready operation and compatible peers.

        Args:
            dag: Native physical operations and dependencies.
            ready: Node IDs with resolved dependencies and true conditions.

        Returns:
            Disjoint addressed operations sharing the earliest operation's
            name, or one CZ/readout operation.
        """
        if not ready:
            return GateLayer(())
        ordered = sorted(ready)
        name = dag.nodes[ordered[0]].name
        if name not in ("rx", "ry", "rz", "reset"):
            return GateLayer((ordered[0],))
        selected: list[int] = []
        occupied: set[int] = set()
        for operation_id in ordered:
            operation = dag.nodes[operation_id]
            if operation.name == name and occupied.isdisjoint(operation.qubits):
                selected.append(operation_id)
                occupied.update(operation.qubits)
        return GateLayer(tuple(selected))
