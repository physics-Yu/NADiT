"""Select a compiler composition independently for each physical circuit."""

from typing import Any

from .device import Device
from .job_fields import integer_at, keys_at, object_at
from .nonzoned.support import require_capabilities
from .physical_ir import PhysicalDAG
from .placement import GatePairCatalog
from .registry import build_strategy
from .strategies import SearchSmarterStrategy
from .strategies.nonzoned import NonZonedStrategy
from .support import UnsupportedJob

type SelectedStrategy = SearchSmarterStrategy | NonZonedStrategy


def select_strategy(
    spec: dict[str, Any],
    *,
    dag: PhysicalDAG,
    device: Device,
    aod_id: str,
    catalog: GatePairCatalog | None,
) -> tuple[SelectedStrategy, dict[str, Any]]:
    """Build a fixed-home compiler or the existing configurable zoned pipeline.

    Args:
        spec: ``kind=nonzoned`` and optional max_cz_gates, or the existing
            scheduler/placement/transport/emitter/reuse stage configuration.
        dag: The complete native DAG, including measurement dependencies.
        device: Geometry, movement, and control capabilities.
        aod_id: Selected transport device.
        catalog: Isolated interaction cells, or None for a catalogue-free job.

    Returns:
        A CompilationStrategy implementation and its resolved configuration.

    Raises:
        ValueError: Invalid options or layer capacity.
        UnsupportedJob: Device capabilities cannot implement the chosen strategy.
    """
    spec = object_at(spec, "strategy")
    if spec.get("kind") == "search_smarter":
        strategy, resolved = build_strategy(
            {key: value for key, value in spec.items() if key != "kind"},
            dag=dag,
            device=device,
            aod_id=aod_id,
            catalog=catalog,
        )
        return strategy, {"kind": "search_smarter", **resolved}
    if "kind" not in spec:
        return build_strategy(
            spec, dag=dag, device=device, aod_id=aod_id, catalog=catalog
        )
    keys_at(spec, {"kind", "max_cz_gates"}, "strategy")
    if spec["kind"] != "nonzoned":
        raise ValueError(f"strategy.kind: unknown compiler {spec['kind']!r}.")
    limit = integer_at(
        spec.get("max_cz_gates", catalog.capacity if catalog else 1),
        "strategy.max_cz_gates",
    )
    if limit < 1:
        raise ValueError("strategy.max_cz_gates: expected a positive integer.")
    require_capabilities(
        device, aod_id, readout=any(n.name == "measure" for n in dag.nodes)
    )
    if catalog is None:
        raise UnsupportedJob(
            [
                {
                    "stage": "strategy",
                    "kind": "nonzoned",
                    "reason": "requires declared shared double-trap cells",
                }
            ]
        )
    return NonZonedStrategy(
        aod_id, catalog, limit, device.readout.max_atoms if device.readout else 1
    ), {
        "kind": "nonzoned",
        "max_cz_gates": limit,
    }
