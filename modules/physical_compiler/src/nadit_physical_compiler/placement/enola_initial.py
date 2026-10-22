"""Generate an optional fixed reference layout before physical compilation."""

from ..device import Device, PairArray
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout, AtomState, SLMHolder
from ..support import UnsupportedJob
from .enola_sa import InteractionLayers, place_initial


def initial_cz_layers(dag: PhysicalDAG) -> InteractionLayers:
    """Project the dependency-known region to CZ-depth layers without sampling.

    Propagate native DAG dependencies, including local gates and barriers.
    Stop each dependency chain at measurement or a conditional node. Independent
    chains remain available, regardless of their order in the source file.
    Only CZ nodes increase CZ depth; initial reset is not a feedback boundary.
    """
    predecessors: list[list[int]] = [[] for _ in dag.nodes]
    for before, after in dag.edges:
        predecessors[after].append(before)
    depths = [0] * len(dag.nodes)
    blocked = [False] * len(dag.nodes)
    layers: list[list[tuple[int, int]]] = []
    for node in dag.nodes:
        if (
            node.name == "measure"
            or node.condition is not None
            or any(blocked[p] for p in predecessors[node.id])
        ):
            blocked[node.id] = True
            continue
        depth = max((depths[p] for p in predecessors[node.id]), default=0)
        if node.name == "cz":
            while len(layers) <= depth:
                layers.append([])
            layers[depth].append((node.qubits[0], node.qubits[1]))
            depth += 1
        depths[node.id] = depth
    return tuple(tuple(layer) for layer in layers)


def reference_array(device: Device, array_id: str | None) -> PairArray:
    """Resolve one declared cell array, requiring selection when ambiguous."""
    if array_id is None and len(device.pair_arrays) == 1:
        return device.pair_arrays[0]
    for array in device.pair_arrays:
        if array.id == array_id:
            return array
    raise UnsupportedJob(
        [
            {
                "stage": "entry",
                "kind": "enola_initial",
                "reason": "select one declared pair array with entry.array",
            }
        ]
    )


def make_enola_initial_layout(
    dag: PhysicalDAG,
    device: Device,
    *,
    array_id: str | None = None,
    seed: int = 0,
    iterations: int = 100,
    moves_per_iteration: int = 400,
) -> AtomLayout:
    """Assign one physical qubit per shared cell using ENOLA initial SA.

    The selected Device pair array maps abstract column/row coordinates to
    actual SLM traps. Home atoms use the left side; right traps remain free.
    This provider returns a reference only, with no atom moves or DAG progress.
    A user-supplied layout bypasses this function entirely.

    Raises:
        UnsupportedJob: Array selection is ambiguous or its capacity is too small.
        ValueError: Optimization budgets are invalid or pinned source changed.
    """
    array = reference_array(device, array_id)
    rows, columns = len(array.left), len(array.left[0])
    if dag.num_qubits > rows * columns:
        raise UnsupportedJob(
            [
                {
                    "stage": "entry",
                    "kind": "enola_initial",
                    "reason": (
                        f"array capacity {rows * columns} is smaller than "
                        f"{dag.num_qubits} qubits"
                    ),
                }
            ]
        )
    mapping = place_initial(
        (columns, rows),
        dag.num_qubits,
        initial_cz_layers(dag),
        seed=seed,
        iterations=iterations,
        moves_per_iteration=moves_per_iteration,
    )
    sites = tuple(device.site(array.left[y][x]) for x, y in mapping)
    return AtomLayout(
        tuple(
            AtomState(q, site.position, SLMHolder(site.id))
            for q, site in enumerate(sites)
        ),
        qubit_to_atom=tuple(range(dag.num_qubits)),
    )
