"""Execute the production compiler and compare observed source pipeline stages."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any

from nadit_physical_compiler.atom_ir import (
    AtomProgram,
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Move,
    Rotation,
    Store,
)
from nadit_physical_compiler.capabilities import (
    CZCapability,
    GlobalControl,
    LocalControl,
)
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.contracts import AtomFragment, GateLayer, PlacementPlan
from nadit_physical_compiler.coverage import RegionSet
from nadit_physical_compiler.device import AOD, Device, Position, Region, Timing, Zone
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.metrics import program_metrics
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    PlacementConfig,
    PlacementGeometry,
)
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer, SourceReuseAnalyzer
from nadit_physical_compiler.scheduling import SourceASAPScheduler
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport
from nadit_physical_compiler.transport.staging_geometry import (
    SiteStagingGeometry,
    StagingGeometry,
)
from reference.placement_candidate import make_geometry

HERE = Path(__file__).resolve().parent
STAGES = ("scheduling", "reuse", "placement", "routing", "actions")


def load_cases(path: Path = HERE / "pipeline_fixtures.json") -> list[dict[str, Any]]:
    """Read shared native circuits and architecture configurations."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return list(data["cases"])


def native_dag(case: dict[str, Any]) -> PhysicalDAG:
    """Build the same prepared native operations and direct wire dependencies."""
    nodes = []
    previous: dict[int, int] = {}
    edges = set()
    for index, operation in enumerate(case["operations"]):
        name = operation["type"].lower()
        qubits = tuple(operation["targets"])
        if name == "p":
            name = "rz"
        if name == "ry" and len(qubits) == case["n_qubits"]:
            name = "global_ry"
        if name == "cz":
            qubits = tuple(sorted(qubits))
        node = PhysicalOperation(
            index, name, qubits, (), tuple(operation.get("parameters", ()))
        )
        nodes.append(node)
        for qubit in qubits:
            if qubit in previous:
                edges.add((previous[qubit], index))
            previous[qubit] = index
    return PhysicalDAG(case["n_qubits"], 0, tuple(nodes), tuple(sorted(edges)))


def _bounds(points: tuple[Position, ...]) -> Region:
    return Region(
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _geometry(case: dict[str, Any]) -> PlacementGeometry:
    return make_geometry(
        {
            "slms": [
                {**slm, "kind": "storage" if slm["pair_group"] < 0 else "gate"}
                for slm in case["slms"]
            ]
        }
    )


def _staging(geometry: PlacementGeometry) -> StagingGeometry:
    sites: dict[str, SiteStagingGeometry] = {}
    groups = [
        (f"storage_{array.id}", (array,), 0.0) for array in geometry.storage_arrays
    ] + [
        (f"gate_{index}", pair, pair[1].origin.x - pair[0].origin.x)
        for index, pair in enumerate(geometry.entanglement_arrays)
    ]
    for name, arrays, separation in groups:
        for array in arrays:
            description = SiteStagingGeometry(
                array.kind,
                array.pitch.x,
                array.pitch.y,
                array.origin.x,
                array.origin.x + array.n_cols * array.pitch.x,
                array.origin.y,
                array.origin.y + array.n_rows * array.pitch.y,
                separation,
                name,
            )
            sites.update((site.id, description) for row in array.sites for site in row)
    return StagingGeometry(sites, coordinate_quantum=1.0)


def _device(
    case: dict[str, Any], geometry: PlacementGeometry
) -> tuple[Device, GatePairCatalog, Region]:
    storage = tuple(
        Zone(
            f"storage_{array.id}",
            Region(
                array.origin.x,
                array.origin.y,
                array.origin.x + array.n_cols * array.pitch.x,
                array.origin.y + array.n_rows * array.pitch.y,
            ),
            ("rotate",),
        )
        for array in geometry.storage_arrays
    )
    rydberg_ranges = case.get("rydberg_range")
    separate_coverage = rydberg_ranges is not None and len(rydberg_ranges) != len(
        geometry.entanglement_arrays
    )
    gate_zones = tuple(
        Zone(
            f"gate_{index}",
            (
                Region(*rydberg_ranges[index][0], *rydberg_ranges[index][1])
                if rydberg_ranges is not None and not separate_coverage
                else _bounds(
                    tuple(
                        site.position
                        for array in pair
                        for row in array.sites
                        for site in row
                    )
                )
            ),
            ("cz", "rotate"),
        )
        for index, pair in enumerate(geometry.entanglement_arrays)
    )
    sites = tuple(
        site
        for array in (
            *geometry.storage_arrays,
            *(array for pair in geometry.entanglement_arrays for array in pair),
        )
        for row in array.sites
        for site in row
    )
    global_region = Region(
        min(zone.bounds.x_min for zone in storage),
        min(zone.bounds.y_min for zone in storage),
        max(zone.bounds.x_max for zone in storage),
        max(zone.bounds.y_max for zone in storage),
    )
    bounds = _bounds(tuple(site.position for site in sites))
    margin = max(
        max(array.pitch.x * array.n_cols, array.pitch.y * array.n_rows)
        for array in (
            *geometry.storage_arrays,
            *(array for pair in geometry.entanglement_arrays for array in pair),
        )
    )
    aod = AOD(
        "transport",
        Region(
            bounds.x_min - margin,
            bounds.y_min - margin,
            bounds.x_max + margin,
            bounds.y_max + margin,
        ),
        **{"min_spacing": 2.0, **case.get("aod", {})},
    )
    timing = Timing(
        **{
            "load_us": 15.0,
            "store_us": 15.0,
            "move_us_per_um": 1.0,
            **case.get("timing", {}),
        }
    )
    device = Device(
        case["name"],
        storage + gate_zones,
        sites,
        (aod,),
        timing,
        CZCapability("rydberg", 4.0, case.get("gate_duration_us", 0.36)),
        local=LocalControl("local", case.get("local_duration_us", 52.0), 1.0),
        global_control=GlobalControl(
            "global",
            case.get("global_duration_us", case.get("local_duration_us", 52.0)),
            region=(
                global_region.x_min,
                global_region.y_min,
                global_region.x_max,
                global_region.y_max,
            ),
        ),
    )
    catalog = GatePairCatalog.from_zones(
        device,
        tuple(
            (
                f"gate_{index}",
                tuple(
                    (left.site(row, column).id, right.site(row, column).id)
                    for row in range(left.n_rows)
                    for column in range(left.n_cols)
                ),
            )
            for index, (left, right) in enumerate(geometry.entanglement_arrays)
        ),
    )
    if separate_coverage and rydberg_ranges is not None:
        regions = tuple(Region(*bounds[0], *bounds[1]) for bounds in rydberg_ranges)
        catalog = replace(
            catalog,
            coverage=regions[0] if len(regions) == 1 else RegionSet(regions),
        )
    return device, catalog, global_region


@dataclass
class _Trace:
    placements: list[list[list[int]]] = field(default_factory=list)
    routings: list[list[list[int]]] = field(default_factory=list)
    reuse: list[list[int]] = field(default_factory=list)
    capture: bool = False


@dataclass
class _ObservedReuse:
    analyzer: SourceReuseAnalyzer | MatchingReuseAnalyzer
    trace: _Trace

    def analyze(
        self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
    ) -> tuple[int, ...]:
        selected = self.analyzer.analyze(dag, current, following)
        self.trace.reuse.append(list(selected))
        return selected


@dataclass
class _ObservedPlacer:
    placer: HeuristicPlacer
    trace: _Trace

    def place(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        reuse_qubits: tuple[int, ...] = (),
        following: GateLayer | None = None,
        retained_atoms: tuple[AtomState, ...] = (),
    ) -> PlacementPlan:
        plan = self.placer.place(
            dag,
            layer,
            layout,
            device,
            reuse_qubits=reuse_qubits,
            following=following,
            retained_atoms=retained_atoms,
        )
        self.trace.capture = bool(
            layer.operation_ids and dag.nodes[layer.operation_ids[0]].name == "cz"
        )
        if self.trace.capture:
            self.trace.placements.extend(
                (_placement(plan.execution_layout), _placement(plan.exit_layout))
            )
        return plan


@dataclass
class _ObservedTransport:
    transporter: StagedStrictTransport | RelaxedTransport
    trace: _Trace

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        fragment = self.transporter.construct(layout, target_layout, device)
        if self.trace.capture:
            groups = []
            carried: set[int] = set()
            batch: set[int] = set()
            for step in fragment.steps:
                for action in step.actions:
                    if isinstance(action, Load):
                        loaded = {atom for atom, _, _ in action.bindings}
                        carried.update(loaded)
                        batch.update(loaded)
                    elif isinstance(action, Store):
                        carried.difference_update(atom for atom, _ in action.bindings)
                        if not carried:
                            groups.append(sorted(batch))
                            batch.clear()
            self.trace.routings.append(groups)
        return fragment


def _placement(layout: AtomLayout) -> list[list[int]]:
    result = []
    for atom in layout.qubit_to_atom:
        holder = layout.atom(atom).holder
        assert isinstance(holder, SLMHolder)
        result.append([int(value) for value in holder.site_id.split(":")])
    return result


def _extent(region: Region) -> list[float]:
    return [region.x_min, region.y_min, region.x_max, region.y_max]


def _actions(
    program: AtomProgram, gate_regions: tuple[Region, ...]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for step in program.steps:
        for action in step.actions:
            event: dict[str, Any] = {
                "kind": "",
                "atoms": [],
                "targets": [],
                "parameters": [],
                "zones": [],
            }
            if isinstance(action, Load):
                event.update(
                    kind="load", atoms=[atom for atom, _, _ in action.bindings]
                )
            elif isinstance(action, Store):
                event.update(kind="store", atoms=[atom for atom, _ in action.bindings])
            elif isinstance(action, Move):
                event.update(
                    kind="move",
                    atoms=[atom for atom, _ in action.targets],
                    targets=[[point.x, point.y] for _, point in action.targets],
                )
            elif isinstance(action, CZPulse):
                event.update(kind="global_cz", zones=list(range(len(gate_regions))))
            elif isinstance(action, GlobalRY):
                event.update(
                    kind="global_ry",
                    parameters=[action.angle],
                    zones=[len(gate_regions)],
                )
            elif isinstance(action, LocalU):
                event.update(
                    kind="local_u",
                    atoms=list(action.atoms),
                    parameters=[action.theta, action.phi, action.lam],
                )
            elif isinstance(action, Rotation):
                event.update(atoms=list(action.atoms))
                if action.axis == "Z":
                    event.update(kind="local_rz", parameters=[action.angle])
                else:
                    phase = math.pi / 2 if action.axis == "X" else 0
                    event.update(
                        kind="local_u", parameters=[action.angle, -phase, phase]
                    )
            else:
                raise ValueError(
                    f"Static pipeline cannot normalize {type(action).__name__}."
                )
            events.append(event)
    return events


def _metrics(program: AtomProgram, elapsed: float) -> dict[str, float | int]:
    shared = program_metrics(program)
    counts = shared["action_counts"]
    actions = [action for step in program.steps for action in step.actions]
    loads = [action for action in actions if isinstance(action, Load)]
    stores = [action for action in actions if isinstance(action, Store)]
    moves = [action for action in actions if isinstance(action, Move)]
    movement = sum(action.duration_us for action in moves)
    transfer = sum(action.duration_us for action in (*loads, *stores))
    return {
        "load_actions": counts.get("Load", 0),
        "store_actions": counts.get("Store", 0),
        "loaded_atoms": shared["atom_load_count"],
        "stored_atoms": shared["atom_store_count"],
        "move_actions": counts.get("Move", 0),
        "movement_us": movement,
        "transfer_us": transfer,
        "rearrangement_us": movement + transfer,
        "total_us": shared["program_duration_us"],
        "compilation_s": elapsed,
    }


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Compile one static fixture through actual modules and observe their handoffs.

    Args:
        case: Shared architecture, native operations, placement/routing config.
            Optional ``rydberg_range`` preserves each source zone's explicit
            ``[[xmin, ymin], [xmax, ymax]]`` coverage independently of trap sites.
            ``reuse_policy='complete_edges'`` explicitly selects the existing
            improved policy; the default is original source reuse.

    Returns:
        Source-compatible stage traces, actions, and AtomProgram timing metrics.
        Algorithms execute once; observers do not rerun search or routing.
    """
    start = perf_counter()
    geometry = _geometry(case)
    device, catalog, global_region = _device(case, geometry)
    dag = native_dag(case)
    settings = case.get("config", {})
    scheduler = SourceASAPScheduler(
        dag, geometry, settings.get("max_filling_factor", 0.9)
    )
    config_fields = PlacementConfig.__dataclass_fields__
    config = PlacementConfig(
        **{key: value for key, value in settings.items() if key in config_fields}
    )
    placer = HeuristicPlacer(
        "transport",
        catalog,
        tuple(f"storage_{array.id}" for array in geometry.storage_arrays),
        config,
        geometry,
    )
    entry = placer.initial_layout(dag.num_qubits, device)
    trace = _Trace([_placement(entry)])
    staging = _staging(geometry)
    transporter: StagedStrictTransport | RelaxedTransport = (
        RelaxedTransport("transport", staging, settings.get("prefer_split", 1.0))
        if settings.get("routing", "relaxed") == "relaxed"
        else StagedStrictTransport("transport", staging)
    )
    reuse = (
        MatchingReuseAnalyzer()
        if case.get("reuse_policy") == "complete_edges"
        else SourceReuseAnalyzer()
    )
    strategy = SearchSmarterStrategy(
        scheduler,
        _ObservedPlacer(placer, trace),
        _ObservedTransport(transporter, trace),
        WholeLayerEmitter(catalog.coverage),
        _ObservedReuse(reuse, trace),
    )
    compiled = compile_physical(dag, device, strategy=strategy, entry_state=entry)
    elapsed = perf_counter() - start
    return {
        "single_layers": [
            list(layer.operation_ids) for layer in scheduler.single_qubit_layers
        ],
        "two_layers": [
            [list(dag.nodes[index].qubits) for index in layer.operation_ids]
            for layer in scheduler.two_qubit_layers
        ],
        "reuse": trace.reuse,
        "placements": trace.placements,
        "routings": trace.routings,
        "zones": [
            *(
                {"name": f"zone_cz{index}", "extent": _extent(region)}
                for index, region in enumerate(catalog.regions)
            ),
            {"name": "global", "extent": _extent(global_region)},
        ],
        "initial": [[atom.position.x, atom.position.y] for atom in entry.atoms],
        "actions": _actions(compiled.program, catalog.regions),
        "metrics": _metrics(compiled.program, elapsed),
    }


def _normalized_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for action in actions:
        item = dict(action)
        atoms = action["atoms"]
        if action["targets"]:
            destinations = sorted(zip(atoms, action["targets"], strict=True))
            item["atoms"] = [atom for atom, _ in destinations]
            item["targets"] = [target for _, target in destinations]
        else:
            item["atoms"] = sorted(atoms)
        normalized.append(item)
    return normalized


def _difference(expected: Any, actual: Any, path: str) -> dict[str, Any] | None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if expected.keys() != actual.keys():
            return {
                "path": path,
                "expected": sorted(expected),
                "actual": sorted(actual),
            }
        for key in expected:
            result = _difference(expected[key], actual[key], f"{path}.{key}")
            if result is not None:
                return result
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return {
                "path": f"{path}.length",
                "expected": len(expected),
                "actual": len(actual),
            }
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            result = _difference(left, right, f"{path}[{index}]")
            if result is not None:
                return result
    elif (
        (isinstance(expected, float) or isinstance(actual, float))
        and isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
    ):
        if not math.isclose(expected, actual, rel_tol=2e-6, abs_tol=2e-6):
            return {"path": path, "expected": expected, "actual": actual}
    elif expected != actual:
        return {"path": path, "expected": expected, "actual": actual}
    return None


def compare_stages(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Report the first differing stage and value, leaving source evidence intact.

    Atom member order within a parallel batch/action is normalized together with
    its destinations. Batch order, action order, sites, coverage and parameters
    remain significant. Metrics are not used as equality evidence.
    """
    if "status" in expected:
        if expected["status"] != "ok":
            completed = expected.get("completed_stages", {})
            for stage in STAGES:
                if stage not in completed:
                    continue
                source_stage = completed[stage]
                candidate_stage = (
                    {
                        "single_layers": actual["single_layers"],
                        "two_layers": actual["two_layers"],
                    }
                    if stage == "scheduling"
                    else actual[
                        {"placement": "placements", "routing": "routings"}.get(
                            stage, stage
                        )
                    ]
                )
                difference = _difference(source_stage, candidate_stage, stage)
                if difference is not None:
                    return {"status": "different", "first_stage": stage, **difference}
            return {
                "status": "source_unavailable",
                "source_status": expected["status"],
                "first_stage": expected.get("failed_stage"),
            }
        expected = expected["result"]
    fields = (
        ("scheduling", ("single_layers", "two_layers")),
        ("reuse", ("reuse",)),
        ("placement", ("placements",)),
        ("routing", ("routings",)),
        ("actions", ("zones", "initial", "actions")),
    )
    for stage, keys in fields:
        for key in keys:
            left, right = expected[key], actual[key]
            if key == "actions":
                left, right = _normalized_actions(left), _normalized_actions(right)
            if key == "routings":
                left, right = (
                    [[sorted(group) for group in transition] for transition in value]
                    for value in (left, right)
                )
            difference = _difference(left, right, key)
            if difference is not None:
                return {"status": "different", "first_stage": stage, **difference}
    return {"status": "matched", "first_stage": None}


def main() -> None:
    """Write observed candidate traces and first-stage comparison results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", type=Path, default=HERE / "pipeline_fixtures.json"
    )
    parser.add_argument(
        "--expected", type=Path, default=HERE / "pipeline_expected.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text(encoding="utf-8"))["cases"]
    observations = {}
    for case in load_cases(args.fixtures):
        result = run_case(case)
        observations[case["name"]] = {
            "trace": result,
            "comparison": compare_stages(expected[case["name"]], result),
        }
    args.output.write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
