"""Strategy admission predicates, separate from compiler execution failures."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .transport.staging_geometry import StagingGeometry

if TYPE_CHECKING:
    from .registry import StrategyContext


@dataclass(frozen=True, slots=True)
class Support:
    """Whether a stage handles a context, with a reason when it does not."""

    ok: bool = True
    reason: str = ""


class UnsupportedJob(ValueError):
    """A syntactically valid job outside the selected strategies' capabilities."""

    def __init__(self, reasons: list[dict[str, str]]) -> None:
        self.reasons = tuple(dict(reason) for reason in reasons)
        super().__init__(
            "; ".join(
                f"{item['stage']}: {item['kind']}: {item['reason']}" for item in reasons
            )
        )

    def document(self) -> dict[str, Any]:
        """Return the CLI's machine-readable unsupported result."""
        return {
            "status": "unsupported",
            "reasons": [dict(item) for item in self.reasons],
        }


def unrestricted(context: "StrategyContext") -> Support:
    """No additional architecture restriction at this stage."""
    return Support()


def pair_only(context: "StrategyContext") -> Support:
    """At most two physical qubits."""
    return Support(context.dag.num_qubits <= 2, "requires at most two physical qubits")


def array_geometry(context: "StrategyContext") -> Support:
    """Infer regular storage and left/right arrays, or supply explicit geometry."""
    try:
        geometry = context.geometry()
    except (ValueError, KeyError, IndexError) as error:
        return Support(False, f"cannot infer storage/pair arrays: {error}")
    return Support(
        bool(geometry.storage_arrays and geometry.entanglement_arrays),
        "requires storage arrays and paired entanglement arrays",
    )


def placement_arrays(context: "StrategyContext") -> Support:
    """CZ needs regular or explicit arrays; local-only placement stays lazy."""
    if not any(node.name == "cz" for node in context.dag.nodes):
        return Support()
    return array_geometry(context)


def source_scheduler(context: "StrategyContext") -> Support:
    """Static circuit with regular or explicit storage/paired entanglement arrays."""
    arrays = array_geometry(context)
    if not arrays.ok:
        return arrays
    if any(
        node.condition is not None or node.name in ("measure", "reset")
        for node in context.dag.nodes
    ):
        return Support(
            False,
            "source_asap requires a static circuit; use ready for measurement feedback",
        )
    return Support()


def flexible(context: "StrategyContext") -> Support:
    """AOD motion must be flexible; rigid motion is unsupported."""
    aod = context.device.aod(context.aod_id)
    return Support(aod.motion == "flexible", f"does not support rigid AOD {aod.id!r}")


def staged(context: "StrategyContext") -> Support:
    """Flexible AOD with inferable storage/left/right arrays or explicit geometry."""
    motion = flexible(context)
    if not motion.ok:
        return motion
    if context.strategy["transport"].get("geometry") is not None:
        return Support()
    geometry = StagingGeometry.from_device(context.device, context.aod_id)
    kinds = {site.kind for site in geometry.sites.values()}
    return Support(
        {"storage", "left", "right"} <= kinds,
        "cannot infer storage/left/right arrays; supply transport.geometry",
    )


def multipair(context: "StrategyContext") -> Support:
    """A gate-pair catalogue and one selected storage zone."""
    if context.catalog is None:
        return Support(False, "requires a gate-pair catalogue")
    storage = context.placement.get("storage_zone_id", context.device.storage_zone_ids)
    names = (storage,) if isinstance(storage, str) else tuple(storage)
    zone_ids = {zone.id for zone in context.device.zones}
    return Support(
        len(names) == 1 and names[0] in zone_ids,
        "requires one storage zone; select storage_zone_id explicitly",
    )


def whole_layer(context: "StrategyContext") -> Support:
    """A gate-pair catalogue or explicit pulse coverage."""
    coverage = context.strategy["emitter"].get("coverage")
    return Support(
        context.catalog is not None or coverage is not None,
        "requires a gate-pair catalogue or explicit coverage",
    )


def ready(context: "StrategyContext") -> Support:
    """Catalogue-sized scheduling requires a gate-pair catalogue."""
    capacity = context.strategy["scheduler"].get("max_cz_gates")
    return Support(
        capacity != "catalog" or context.catalog is not None,
        "max_cz_gates=catalog requires a gate-pair catalogue",
    )
