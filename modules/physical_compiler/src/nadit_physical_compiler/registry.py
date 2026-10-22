"""Construct existing strategy stages from named, independently scoped options."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from . import support
from .device import Device
from .emission import WholeLayerEmitter, emit_layer
from .job_fields import (
    boolean_at,
    integer_at,
    keys_at,
    number_at,
    object_at,
    required,
    string_at,
)
from .job_geometry import (
    placement_geometry,
    region_coverage,
    resolve_geometry_defaults,
    staging_geometry,
)
from .physical_ir import PhysicalDAG
from .placement import (
    GatePairCatalog,
    HeuristicPlacer,
    MultiPairPlacer,
    PairPlacer,
    PlacementConfig,
    PlacementGeometry,
)
from .readout import BatchReadout
from .reuse import MatchingReuseAnalyzer, SourceReuseAnalyzer
from .scheduling import ReadyScheduler, SourceASAPScheduler
from .strategies import SearchSmarterStrategy
from .support import Support, UnsupportedJob
from .transport import (
    PairTransport,
    RelaxedTransport,
    SequentialTransport,
    StagedStrictTransport,
    StrictTransport,
)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Objects already supplied by the job, shared across stage constructors."""

    dag: PhysicalDAG
    device: Device
    aod_id: str
    catalog: GatePairCatalog | None
    placement: dict[str, Any]
    strategy: dict[str, Any] = field(default_factory=dict)

    def require_catalog(self) -> GatePairCatalog:
        """Require actual declared gate pairs only for a stage using them."""
        if self.catalog is None:
            raise ValueError("gate_zones: this stage requires a gate-pair catalogue.")
        return self.catalog

    def geometry(self) -> PlacementGeometry:
        """Use explicit placement geometry or infer it only when needed."""
        path = "strategy.placement.geometry"
        explicit = placement_geometry(self.placement.get("geometry"), self.device, path)
        if explicit is not None:
            return explicit
        storage = self.placement.get("storage_zone_id", self.device.storage_zone_ids)
        return PlacementGeometry.from_device(
            self.device,
            self.aod_id,
            self.require_catalog(),
            tuple(storage) if isinstance(storage, list) else storage,
        )


@dataclass(frozen=True, slots=True)
class Registration:
    """One implementation's factory and constructor defaults.

    Add an entry to REGISTRY to register a stage implementation. Its options
    use JSON scalar types, plus the existing geometry and coverage records.
    ``supports`` declares admission requirements; its docstring is exposed by
    ``nadit describe``. Every predicate runs before any stage factory.
    """

    factory: Callable[[StrategyContext, dict[str, Any]], Any]
    defaults: dict[str, Any]
    supports: Callable[[StrategyContext], Support] = support.unrestricted


def _heuristic(context: StrategyContext, options: dict[str, Any]) -> HeuristicPlacer:
    config_values = {
        key: value
        for key, value in options.items()
        if key in PlacementConfig.__dataclass_fields__
    }
    storage = options["storage_zone_id"]
    return HeuristicPlacer(
        context.aod_id,
        context.require_catalog(),
        tuple(storage) if isinstance(storage, list) else storage,
        _placement_config(config_values),
        placement_geometry(
            options["geometry"], context.device, "strategy.placement.geometry"
        ),
    )


def _placement_config(values: dict[str, Any]) -> PlacementConfig:
    try:
        return PlacementConfig(**values)
    except ValueError as error:
        defaults = PlacementConfig.for_method(values["method"])
        for name, value in values.items():
            try:
                replace(defaults, **{name: value})
            except ValueError as field_error:
                raise ValueError(f"strategy.placement.{name}: {field_error}") from error
        raise


def _search_defaults(method: Literal["ids", "astar"]) -> dict[str, Any]:
    return {
        **asdict(PlacementConfig.for_method(method)),
        "storage_zone_id": "storage",
        "geometry": None,
    }


REGISTRY: dict[str, dict[str, Registration]] = {
    "scheduler": {
        "ready": Registration(
            lambda context, options: ReadyScheduler(
                options["max_cz_gates"],
                max_measurements=context.device.readout.max_atoms
                if context.device.readout
                else 1,
            ),
            {"max_cz_gates": 1},
            support.ready,
        ),
        "source_asap": Registration(
            lambda context, options: SourceASAPScheduler(
                context.dag, context.geometry(), options["max_filling_factor"]
            ),
            {"max_filling_factor": 0.9},
            support.source_scheduler,
        ),
    },
    "placement": {
        "pair": Registration(
            lambda context, options: PairPlacer(context.aod_id), {}, support.pair_only
        ),
        "multipair": Registration(
            lambda context, options: MultiPairPlacer(
                context.aod_id, context.require_catalog(), options["storage_zone_id"]
            ),
            {"storage_zone_id": "storage"},
            support.multipair,
        ),
        "ids": Registration(
            _heuristic, _search_defaults("ids"), support.placement_arrays
        ),
        "astar": Registration(
            _heuristic, _search_defaults("astar"), support.placement_arrays
        ),
    },
    "transport": {
        "pair": Registration(
            lambda context, options: PairTransport(context.aod_id),
            {},
            support.pair_only,
        ),
        "sequential": Registration(
            lambda context, options: SequentialTransport(context.aod_id), {}
        ),
        "strict": Registration(
            lambda context, options: StrictTransport(context.aod_id), {}
        ),
        "staged": Registration(
            lambda context, options: StagedStrictTransport(
                context.aod_id,
                staging_geometry(
                    options["geometry"], context.device, "strategy.transport.geometry"
                ),
            ),
            {"geometry": None},
            support.staged,
        ),
        "relaxed": Registration(
            lambda context, options: RelaxedTransport(
                context.aod_id,
                staging_geometry(
                    options["geometry"], context.device, "strategy.transport.geometry"
                ),
                options["prefer_split"],
            ),
            {"geometry": None, "prefer_split": 1.0},
            support.flexible,
        ),
    },
    "emitter": {
        "layer": Registration(lambda context, options: emit_layer, {}),
        "whole_layer": Registration(
            lambda context, options: WholeLayerEmitter(
                context.require_catalog().coverage
                if options["coverage"] is None
                else region_coverage(options["coverage"], "strategy.emitter.coverage")
            ),
            {"coverage": None},
            support.whole_layer,
        ),
    },
    "reuse": {
        "none": Registration(lambda context, options: None, {}),
        "matching": Registration(lambda context, options: MatchingReuseAnalyzer(), {}),
        "source": Registration(lambda context, options: SourceReuseAnalyzer(), {}),
    },
}


def build_strategy(
    spec: dict[str, Any],
    *,
    dag: PhysicalDAG,
    device: Device,
    aod_id: str,
    catalog: GatePairCatalog | None,
) -> tuple[SearchSmarterStrategy, dict[str, Any]]:
    """Build a strategy and fully resolved stage options, without compiling.

    Args:
        spec: Each stage's kind and constructor/PlacementConfig options.
        dag: Parsed native circuit, used by the source scheduler.
        device: Capabilities and geometry from the job.
        aod_id: Selected transport AOD.
        catalog: Declared gate pairs, or None for a job needing none.

    Returns:
        The existing composed strategy and its JSON configuration with defaults.

    Raises:
        ValueError: Invalid kinds, fields, or values, with their job paths.
        UnsupportedJob: Unsupported stages and reasons, before construction.
    """
    spec = object_at(spec, "strategy")
    keys_at(spec, set(REGISTRY) | {"mode"}, "strategy")
    mode = string_at(spec.get("mode", "adaptive"), "strategy.mode")
    if mode not in ("adaptive", "qec"):
        raise ValueError("strategy.mode: expected 'adaptive' or 'qec'.")
    resolved: dict[str, Any] = {}
    for stage, implementations in REGISTRY.items():
        path = f"strategy.{stage}"
        stage_spec = object_at(required(spec, stage, "strategy"), path)
        kind = string_at(required(stage_spec, "kind", path), f"{path}.kind")
        if kind not in implementations:
            raise ValueError(f"{path}.kind: unknown implementation {kind!r}.")
        registration = implementations[kind]
        keys_at(stage_spec, {"kind", *registration.defaults}, path)
        options = {
            name: _option(
                stage_spec.get(name, default), default, f"{path}.{name}", kind
            )
            for name, default in registration.defaults.items()
        }
        if (
            stage == "scheduler"
            and options.get("max_cz_gates") == "catalog"
            and catalog is not None
        ):
            options["max_cz_gates"] = catalog.capacity
        if (
            stage == "placement"
            and "storage_zone_id" in options
            and "storage_zone_id" not in stage_spec
        ):
            names = device.storage_zone_ids
            options["storage_zone_id"] = names[0] if len(names) == 1 else list(names)
        if stage == "placement" and "method" in options and options["method"] != kind:
            raise ValueError(f"{path}.method: must agree with kind {kind!r}.")
        resolved[stage] = {"kind": kind, **options}
    if mode == "qec":
        resolved["reuse"] = {"kind": "none"}
    context = StrategyContext(
        dag, device, aod_id, catalog, resolved["placement"], resolved
    )
    # Malformed explicit geometry is input error, not unsupported architecture.
    placement_geometry(
        resolved["placement"].get("geometry"), device, "strategy.placement.geometry"
    )
    staging_geometry(
        resolved["transport"].get("geometry"), device, "strategy.transport.geometry"
    )
    if resolved["emitter"].get("coverage") is not None:
        region_coverage(resolved["emitter"]["coverage"], "strategy.emitter.coverage")
    failures = []
    for stage, options in resolved.items():
        checked = REGISTRY[stage][options["kind"]].supports(context)
        if not checked.ok:
            failures.append(
                {
                    "stage": f"strategy.{stage}",
                    "kind": options["kind"],
                    "reason": checked.reason,
                }
            )
    if failures:
        raise UnsupportedJob(failures)
    objects: dict[str, Any] = {}
    for stage, options in resolved.items():
        kind = options["kind"]
        try:
            objects[stage] = REGISTRY[stage][kind].factory(
                context, {key: value for key, value in options.items() if key != "kind"}
            )
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ValueError(f"strategy.{stage}: {error}") from error
    if "geometry" in resolved["placement"]:
        resolved["placement"]["geometry"] = resolve_geometry_defaults(
            resolved["placement"]["geometry"], stage="placement"
        )
    if "geometry" in resolved["transport"]:
        resolved["transport"]["geometry"] = resolve_geometry_defaults(
            resolved["transport"]["geometry"], stage="transport"
        )
    if "mode" in spec:
        resolved["mode"] = mode
    return SearchSmarterStrategy(
        objects["scheduler"],
        objects["placement"],
        objects["transport"],
        objects["emitter"],
        objects["reuse"],
        BatchReadout(aod_id)
        if device.readout is not None and device.readout.max_atoms != 1
        else None,
        mode="qec" if mode == "qec" else "adaptive",
    ), resolved


def _option(value: Any, default: Any, path: str, kind: str) -> Any:
    name = path.rsplit(".", 1)[-1]
    if name == "max_cz_gates" and value == "catalog":
        return value
    if (
        name == "storage_zone_id"
        and kind in ("ids", "astar")
        and isinstance(value, list)
    ):
        return [string_at(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if default is None:
        return None if value is None else object_at(value, path)
    if isinstance(default, bool):
        return boolean_at(value, path)
    if name in (
        "deepening_factor",
        "deepening_value",
        "lookahead_factor",
        "reuse_level",
    ) or isinstance(default, float):
        return number_at(value, path)
    if isinstance(default, int):
        return integer_at(value, path)
    return string_at(value, path)
