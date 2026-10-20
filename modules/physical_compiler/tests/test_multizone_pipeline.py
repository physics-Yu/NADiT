"""Multiple CZ regions and source controls retain legal atom-action handoffs."""

from dataclasses import replace
from itertools import combinations
from math import hypot

import pytest
from test_strict_compiler import RecordingBackend

from nadit_physical_compiler import (
    AOD,
    CZCapability,
    CZPulse,
    Device,
    GlobalControl,
    GlobalRY,
    LocalControl,
    LocalU,
    Position,
    ReadoutCapability,
    Region,
    RegionSet,
    SLMSite,
    Timing,
    Zone,
    compile_physical,
    slm_grid,
)
from nadit_physical_compiler.atom_ir import Load, Measure, Move, Store
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.physical_ir import (
    Condition,
    PhysicalDAG,
    PhysicalOperation,
    ResultRef,
)
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    PlacementConfig,
    PlacementGeometry,
)
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport


def _device() -> Device:
    zones: list[Zone] = []
    sites: list[SLMSite] = []
    for name, x in (("a", 0), ("b", 100)):
        zones.extend(
            (
                Zone(f"storage_{name}", Region(x, 0, x + 15, 10), ("rotate",)),
                Zone(f"gate_{name}", Region(x, 99, x + 14, 111), ("cz", "rotate")),
            )
        )
        sites.extend(slm_grid(f"s{name}", Position(x, 0), 3, 4, Position(5, 5)))
        for side, offset in (("left", 0), ("right", 2)):
            sites.extend(
                slm_grid(
                    f"g{name}_{side}", Position(x + offset, 100), 2, 2, Position(12, 10)
                )
            )
    zones.extend(
        (
            Zone("gap", Region(50, 99, 60, 101), ("rotate",)),
            Zone("readout", Region(0, 150, 140, 160), ("measure",)),
        )
    )
    sites.append(SLMSite("spectator", Position(55, 100)))
    return Device(
        "two-cz-zones",
        tuple(zones),
        tuple(sites),
        (AOD("transport", Region(-20, -20, 160, 180), 1),),
        Timing(15, 15, 1),
        cz=CZCapability("rydberg", 4.6, 0.36),
        local=LocalControl("local", 2, 3),
        readout=ReadoutCapability("readout", 5, 7),
        global_control=GlobalControl("global", 2),
    )


def _catalog(device: Device) -> GatePairCatalog:
    return GatePairCatalog.from_zones(
        device,
        tuple(
            (
                f"gate_{name}",
                tuple(
                    (f"g{name}_left_r{row}_c{col}", f"g{name}_right_r{row}_c{col}")
                    for row in range(2)
                    for col in range(2)
                ),
            )
            for name in ("a", "b")
        ),
    )


def _entry(device: Device) -> AtomLayout:
    ids = (31, 7, 83, 19, 101)
    names = ("sa_r2_c0", "sa_r2_c1", "sb_r2_c0", "sb_r2_c1", "spectator")
    return AtomLayout(
        tuple(
            AtomState(atom, device.site(name).position, SLMHolder(name))
            for atom, name in zip(ids, names, strict=True)
        ),
        qubit_to_atom=ids,
    )


def _dag() -> PhysicalDAG:
    operations = [
        ("global_ry", tuple(range(5)), (0.25,), None),
        ("u", (0,), (0.1, 0.2, 0.3), None),
        ("cz", (0, 1), (), None),
        ("cz", (2, 3), (), None),
        ("global_ry", tuple(range(5)), (-0.25,), None),
        ("cz", (0, 1), (), None),
        ("cz", (2, 3), (), None),
        ("measure", (0,), (), None),
        ("u", (2,), (0.3, 0.2, 0.1), Condition(ResultRef(7))),
    ]
    nodes = tuple(
        PhysicalOperation(
            index, name, qubits, (0,) if name == "measure" else (), params, condition
        )
        for index, (name, qubits, params, condition) in enumerate(operations)
    )
    edges = set()
    previous: dict[int, int] = {}
    for node in nodes:
        for qubit in node.qubits:
            if qubit in previous:
                edges.add((previous[qubit], node.id))
            previous[qubit] = node.id
        if node.condition is not None:
            edges.add((node.condition.result.measurement_id, node.id))
    return PhysicalDAG(5, 1, nodes, tuple(sorted(edges)))


@pytest.mark.parametrize("relaxed", [False, True])
@pytest.mark.parametrize("bit", [0, 1])
def test_multizone_pulses_source_controls_feedback_and_replay(
    relaxed: bool, bit: int
) -> None:
    device = _device()
    catalog = _catalog(device)
    geometry = PlacementGeometry.from_device(
        device, "transport", catalog, ("storage_a", "storage_b")
    )
    assert len(geometry.storage_arrays) == len(geometry.entanglement_arrays) == 2
    assert catalog.capacity == 8
    assert isinstance(catalog.coverage, RegionSet)
    assert not catalog.coverage.contains(device.site("spectator").position)
    dag = _dag()
    entry = _entry(device)
    strategy = SearchSmarterStrategy(
        ReadyScheduler(2),
        HeuristicPlacer(
            "transport",
            catalog,
            ("storage_a", "storage_b"),
            PlacementConfig(use_window=False),
            geometry,
        ),
        RelaxedTransport("transport")
        if relaxed
        else StagedStrictTransport("transport"),
        WholeLayerEmitter(catalog.coverage),
        MatchingReuseAnalyzer(),
    )
    backend = RecordingBackend(bit)
    result = compile_physical(
        dag, device, strategy=strategy, entry_state=entry, measurement_backend=backend
    )
    layout = entry
    pulses = []
    globals_ = []
    local = []
    for start, step in result.program.timeline():
        for action in step.actions:
            if isinstance(action, Load):
                checked = check_load(
                    device,
                    layout,
                    action.grid,
                    {atom: (row, column) for atom, row, column in action.bindings},
                )
                assert checked.ok, checked.message
            elif isinstance(action, Move):
                checked = check_move(device, layout, action.grid, dict(action.targets))
                assert checked.ok, checked.message
            elif isinstance(action, Store):
                checked = check_store(
                    device, layout, action.grid, dict(action.bindings)
                )
                assert checked.ok, checked.message
            elif isinstance(action, CZPulse):
                assert action.coverage == catalog.coverage
                illuminated = [
                    atom
                    for atom in layout.atoms
                    if catalog.coverage.contains(atom.position)
                ]
                actual = {
                    frozenset((a.id, b.id))
                    for a, b in combinations(illuminated, 2)
                    if hypot(a.position.x - b.position.x, a.position.y - b.position.y)
                    <= 4.6
                }
                expected = {
                    frozenset(entry.qubit_to_atom[q] for q in dag.nodes[index].qubits)
                    for index in action.source_ids
                }
                assert actual == expected
                assert all(
                    any(region.contains(atom.position) for atom in illuminated)
                    for region in catalog.regions
                )
                pulses.append(action)
            elif isinstance(action, GlobalRY):
                assert all(
                    action.coverage.contains(atom.position) for atom in layout.atoms
                )
                globals_.append(action)
            elif isinstance(action, LocalU):
                local.append(action)
                if action.source_ids == (8,):
                    assert start >= result.measurements[0].available_at_us
            elif isinstance(action, Measure):
                assert action.results == ((entry.qubit_to_atom[0], ResultRef(7)),)
        layout = apply_step(layout, step, device)
        assert layout.atom(101) == entry.atom(101)
    assert len(pulses) == len(globals_) == 2
    assert len(local) == 1 + bit
    assert backend.calls == [ResultRef(7)]
    assert result.operations[8].status == ("emitted" if bit else "skipped")
    assert replay(result.program, entry, device) == layout == result.exit_state
    text = result.program.to_text()
    assert "regions=[[0,99,14,111],[100,99,114,111]]" in text
    assert "GLOBAL_RY(" in text and "U(" in text


def test_explicit_global_illumination_keeps_the_source_storage_only_profile() -> None:
    from nadit_physical_compiler.contracts import GateLayer
    from nadit_physical_compiler.emission.actions import emit_layer

    device = _device()
    device = replace(
        device, global_control=GlobalControl("global", 2, region=(0, 0, 120, 15))
    )
    action = (
        emit_layer(_dag(), GateLayer((0,)), _entry(device), device).steps[0].actions[0]
    )
    assert isinstance(action, GlobalRY)
    assert action.coverage == Region(0, 0, 120, 15)
    assert not action.coverage.contains(Position(0, 100))


def test_empty_gap_between_regions_is_not_illuminated() -> None:
    coverage = RegionSet((Region(0, 0, 2, 2), Region(10, 0, 12, 2)))
    assert coverage.contains(Position(1, 1))
    assert coverage.contains(Position(11, 1))
    assert not coverage.contains(Position(5, 1))


def test_overlapping_pulse_regions_do_not_merge_or_duplicate_slm_arrays() -> None:
    device = _device()
    device = replace(
        device,
        zones=tuple(
            replace(zone, bounds=Region(0, 99, 114, 111))
            if zone.id.startswith("gate_")
            else zone
            for zone in device.zones
        ),
    )
    catalog = _catalog(device)
    geometry = PlacementGeometry.from_device(
        device, "transport", catalog, ("storage_a", "storage_b")
    )
    assert [len(pairs) for pairs in catalog.region_pairs] == [4, 4]
    assert [left.origin.x for left, _ in geometry.entanglement_arrays] == [0, 100]
    ids = [
        site.id
        for arrays in geometry.entanglement_arrays
        for array in arrays
        for row in array.sites
        for site in row
    ]
    assert len(ids) == len(set(ids)) == 16
