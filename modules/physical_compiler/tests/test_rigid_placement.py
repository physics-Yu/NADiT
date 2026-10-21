"""Placement scoring uses the same rigid compatibility as transport."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.device import (
    AOD,
    Position,
    Region,
    SLMSite,
    Timing,
    load_device,
)
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement import heuristic
from nadit_physical_compiler.placement.catalog import GatePairCatalog
from nadit_physical_compiler.placement.heuristic import HeuristicPlacer
from nadit_physical_compiler.placement.scoring import (
    PlacementConfig,
    PlacementCoordinates,
    PlacementJob,
    PlacementOption,
    _Node,
    _Search,
    search_placement,
)
from nadit_physical_compiler.transport.rules import (
    MoveRequest,
    RigidRules,
    StrictRules,
    TransportRules,
    rules_for,
)


def _jobs() -> tuple[PlacementJob, ...]:
    first = PlacementOption(
        ((7, SLMSite("first", Position(10, 20))),),
        (MoveRequest(7, Position(0, 0), Position(10, 20)),),
    )
    compressed = PlacementOption(
        ((11, SLMSite("compressed", Position(12, 20))),),
        (MoveRequest(11, Position(5, 0), Position(12, 20)),),
    )
    translated = PlacementOption(
        ((11, SLMSite("translated", Position(15, 20))),),
        (MoveRequest(11, Position(5, 0), Position(15, 20)),),
        lookahead_cost=0.1,
    )
    return (PlacementJob((first,)), PlacementJob((compressed, translated)))


@pytest.mark.parametrize("method", ["ids", "astar"])
def test_search_prefers_shared_rigid_displacement_over_flexible_compression(
    method: Literal["ids", "astar"],
) -> None:
    jobs = _jobs()
    aod = AOD("a", Region(-100, -100, 100, 100), 1)
    timing = Timing(1, 1, 1)
    config = PlacementConfig.for_method(method)
    flexible = rules_for(aod, timing)
    rigid = rules_for(replace(aod, motion="rigid"), timing)

    flexible_choice = search_placement(jobs, flexible, config)
    rigid_choice = search_placement(jobs, rigid, config)

    assert flexible_choice == (jobs[0].options[0], jobs[1].options[0])
    assert rigid_choice == (jobs[0].options[0], jobs[1].options[1])
    fixed_jobs = tuple(PlacementJob((job.options[0],)) for job in jobs)
    searches = tuple(
        _Search(fixed_jobs, rules, config, "gate") for rules in (flexible, rigid)
    )
    nodes = []
    for search in searches:
        node = _Node()
        for _ in fixed_jobs:
            node = next(iter(search.expand(node)))
        nodes.append(node)
    assert [len(node.groups) for node in nodes] == [1, 2]
    assert searches[0].cost(nodes[0]) < searches[1].cost(nodes[1])


@pytest.mark.parametrize(
    ("motion", "kind"), [("flexible", StrictRules), ("rigid", RigidRules)]
)
def test_heuristic_passes_selected_motion_rules_to_gate_and_storage_search(
    monkeypatch: pytest.MonkeyPatch,
    motion: Literal["flexible", "rigid"],
    kind: type[StrictRules],
) -> None:
    path = Path(__file__).resolve().parents[1] / "configs" / "multipair_flexible.json"
    device = load_device(path)
    device = replace(
        device, aods=tuple(replace(aod, motion=motion) for aod in device.aods)
    )
    catalog = GatePairCatalog.from_device(
        device,
        "entanglement",
        (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right")),
    )
    placer = HeuristicPlacer("transport", catalog)
    dag = PhysicalDAG(
        4,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "cz", (2, 3), (), ()),
        ),
        (),
    )
    entry = placer.initial_layout(dag.num_qubits, device)
    observed: list[tuple[str, type[object]]] = []

    def capture(
        jobs: tuple[PlacementJob, ...],
        rules: TransportRules,
        config: PlacementConfig,
        *,
        stage: Literal["gate", "storage"] = "gate",
        coordinates: PlacementCoordinates | None = None,
    ) -> tuple[PlacementOption, ...]:
        observed.append((stage, type(rules)))
        return search_placement(
            jobs, rules, config, stage=stage, coordinates=coordinates
        )

    monkeypatch.setattr(heuristic, "search_placement", capture)
    plan = placer.place(dag, GateLayer((0, 1)), entry, device)

    assert observed == [("gate", kind), ("storage", kind)]
    assert len(plan.execution_layout.atoms) == len(plan.exit_layout.atoms) == 4
