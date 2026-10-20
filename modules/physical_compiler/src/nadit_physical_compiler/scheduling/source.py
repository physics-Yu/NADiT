"""Static ASAP layers adapted from the pinned MQT QMAP scheduler.

Reference: MQT QMAP cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d (MIT).
"""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from math import floor, isfinite

from ..contracts import GateLayer
from ..physical_ir import PhysicalDAG
from ..placement.geometry import PlacementGeometry


def source_capacity(
    geometry: PlacementGeometry, max_filling_factor: float = 0.9
) -> int:
    """Calculate source CZ capacity by summing each gate zone's rounded share.

    Args:
        geometry: Ordered paired entanglement arrays.
        max_filling_factor: Allowed share in each zone, between zero and one.

    Returns:
        Sum of ``max(1, floor(factor * rows * columns))`` across gate zones.

    Raises:
        ValueError: If the factor is outside its domain or no gate zone exists.
    """
    if not isfinite(max_filling_factor) or not 0 <= max_filling_factor <= 1:
        raise ValueError("max_filling_factor must lie in [0, 1].")
    capacity = sum(
        max(1, floor(max_filling_factor * left.n_rows * left.n_cols))
        for left, _ in geometry.entanglement_arrays
    )
    if capacity == 0:
        raise ValueError("Source scheduling requires an entanglement zone.")
    return capacity


class SourceASAPScheduler:
    """Precompute original static layers while leaving DAG progress to the compiler.

    Args:
        dag: Static native circuit. Conditions, measurement, and reset belong to
            the existing online scheduler rather than this source policy.
        geometry: Entanglement arrays used by the source capacity calculation.
        max_filling_factor: Per-zone occupied share, defaulting to source 0.9.

    ``select`` only reads the supplied frontier. Repeated calls and lookahead
    cannot change future decisions. Local operations retain their input order
    inside each source single-qubit phase; CZ operations form its parallel phase.
    """

    def __init__(
        self,
        dag: PhysicalDAG,
        geometry: PlacementGeometry,
        max_filling_factor: float = 0.9,
    ) -> None:
        self.max_cz_gates = source_capacity(geometry, max_filling_factor)
        single, two = _schedule(dag, self.max_cz_gates)
        self.single_qubit_layers = tuple(GateLayer(tuple(ids)) for ids in single)
        self.two_qubit_layers = tuple(GateLayer(tuple(ids)) for ids in two)
        self._priority: dict[int, tuple[int, int]] = {}
        for phase, layer in enumerate(self.single_qubit_layers):
            for order, index in enumerate(layer.operation_ids):
                self._priority[index] = (2 * phase, order)
        for phase, layer in enumerate(self.two_qubit_layers):
            for index in layer.operation_ids:
                self._priority[index] = (2 * phase + 1, 0)

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Select the earliest source phase among currently ready operations.

        Args:
            dag: Circuit used to construct this scheduler.
            ready: Resolved node IDs supplied by compilation or lookahead.

        Returns:
            One local operation or the ready operations in the next CZ phase.
            Barriers and global phase metadata are completed by the compiler.
        """
        candidates = [index for index in ready if index in self._priority]
        if not candidates:
            return GateLayer(())
        first = min(candidates, key=self._priority.__getitem__)
        phase = self._priority[first][0]
        if dag.nodes[first].name != "cz":
            return GateLayer((first,))
        return GateLayer(
            tuple(
                index
                for index in self.two_qubit_layers[phase // 2].operation_ids
                if index in ready
            )
        )


def _schedule(
    dag: PhysicalDAG, capacity: int
) -> tuple[list[list[int]], list[list[int]]]:
    if not dag.nodes:
        return [], []
    single: list[list[int]] = [[]]
    two: list[list[int]] = []
    next_layer = [0] * dag.num_qubits
    for operation in dag.nodes:
        name = operation.name
        if operation.condition is not None:
            raise ValueError("Source static scheduling does not resolve conditions.")
        if name == "global_phase":
            continue
        if name == "barrier":
            if len(set(operation.qubits)) != dag.num_qubits:
                raise ValueError("Source static scheduling requires global barriers.")
            next_layer[:] = [len(two)] * dag.num_qubits
        elif name == "global_ry":
            phase = max(next_layer, default=0)
            next_layer[:] = [phase] * dag.num_qubits
            single[phase].append(operation.id)
        elif name in ("rx", "ry", "rz", "u") and len(operation.qubits) == 1:
            single[next_layer[operation.qubits[0]]].append(operation.id)
        elif name == "cz" and len(operation.qubits) == 2:
            first, second = operation.qubits
            phase = max(next_layer[first], next_layer[second])
            while phase < len(two) and len(two[phase]) >= capacity:
                phase += 1
            if phase == len(two):
                single.append([])
                two.append([])
            two[phase].append(operation.id)
            next_layer[first] = next_layer[second] = phase + 1
        else:
            raise ValueError(f"Unsupported static source operation: {name!r}.")
    return single, two
