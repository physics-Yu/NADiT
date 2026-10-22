"""Bounded initial placement using the pinned ENOLA SAPlacer class."""

import ast
import math
import sys
from hashlib import sha256
from importlib.resources import files
from random import Random
from typing import Any

ENOLA_COMMIT = "2944dbf4e163e8d2eeeec607add0d9139edce689"
PLACER_SHA256 = "d256c84490bd72d525515f24acb081d7b21bcd5f7bbc31302a3bbf79a16997cf"
type InteractionLayers = tuple[tuple[tuple[int, int], ...], ...]


def _source_class(seed: int) -> Any:
    raw = files(__package__).joinpath("enola_source/placer.py.txt").read_bytes()
    if sha256(raw).hexdigest() != PLACER_SHA256:
        raise ValueError("Pinned ENOLA initial placer source has changed.")
    nodes = [
        node
        for node in ast.parse(raw).body
        if isinstance(node, ast.ClassDef) and node.name == "SAPlacer"
    ]
    rng = Random(seed)
    scope: dict[str, Any] = {
        "math": math,
        "sys": sys,
        "randrange": rng.randrange,
        "shuffle": rng.shuffle,
        "uniform": rng.uniform,
        "print": lambda *args: None,
    }
    exec(
        compile(ast.Module(body=[*nodes], type_ignores=[]), "ENOLA/placer.py", "exec"),
        scope,
    )
    return scope["SAPlacer"]


def place_initial(
    shape: tuple[int, int],
    n_qubits: int,
    layers: InteractionLayers,
    *,
    seed: int = 0,
    iterations: int = 100,
    moves_per_iteration: int = 400,
) -> tuple[tuple[int, int], ...]:
    """Return qubit-indexed (column, row) homes from one initial SA run.

    Shape and capacity are supplied by the Device-array adapter. ENOLA's
    distance objective, domain clipping, proposals, and temperature schedule
    are retained. Budgets count temperature iterations and proposals within
    each iteration; the original 100-proposal warm-up precedes them.
    Empty interaction graphs use row-major assignment. A warm-up with no
    sampled uphill proposal terminates at its best placement without division
    by zero. RNG functions are local; global Python random state is untouched.
    """
    if any(type(v) is not int or v < 1 for v in (iterations, moves_per_iteration)):
        raise ValueError("Initial placement budgets must be positive integers.")
    if shape[0] < 1 or shape[1] < 1 or not 0 <= n_qubits <= shape[0] * shape[1]:
        raise ValueError("Initial placement shape must fit all qubits.")
    if not any(layers):
        return tuple((q % shape[0], q // shape[0]) for q in range(n_qubits))
    base = _source_class(seed)

    class BoundedPlacer(base):  # type: ignore[misc, valid-type]
        def initialize_param(self) -> None:
            super().initialize_param()
            # Upstream stops when sa_n > sa_iter_limit, after that iteration.
            self.sa_iter_limit = iterations - 1
            self.sa_l = moves_per_iteration

        def init_perturb(self) -> None:
            uphill_sum, uphill_count = 0.0, 0
            for _ in range(self.sa_init_perturb_num):
                self.make_movement()
                self.current_cost += self.sa_delta
                if self.best_cost - self.current_cost > 1e-9:
                    self.update_optimal_sol()
                if self.sa_delta > 0:
                    uphill_sum += self.sa_delta
                    uphill_count += 1
            self.sa_t1 = (
                uphill_sum / uphill_count / -math.log(self.sa_p)
                if uphill_count
                else 0.0
            )
            self.sa_t = self.sa_t1

    placer = BoundedPlacer()
    placer.run(shape, n_qubits, layers)
    return tuple((int(x), int(y)) for x, y in placer.best_mapping)
