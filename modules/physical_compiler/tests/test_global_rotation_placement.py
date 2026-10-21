"""Global rotation placement matches the configured illuminated atom set."""

from dataclasses import replace

import pytest

from nadit_physical_compiler import (
    AOD,
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    CZCapability,
    Device,
    GlobalControl,
    GlobalRY,
    Position,
    Region,
    SLMHolder,
    SLMSite,
    Timing,
    Zone,
    compile_physical,
)
from nadit_physical_compiler.contracts import GateLayer
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.replay import apply_step
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import PairTransport, SequentialTransport
from nadit_physical_compiler.validation import validate_result


def _device() -> Device:
    return Device(
        "global-rotation-placement",
        (
            Zone("storage", Region(-1, -1, 16, 1), ("rotate",)),
            Zone("gate", Region(-1, 99, 11, 101), ("cz", "rotate")),
        ),
        tuple(SLMSite(f"s{index}", Position(5 * index, 0)) for index in range(4))
        + (
            SLMSite("g0", Position(0, 100)),
            SLMSite("g1", Position(2, 100)),
            SLMSite("outside", Position(10, 100)),
        ),
        (AOD("transport", Region(-5, -5, 30, 110), 1),),
        Timing(15, 15, 1),
        cz=CZCapability("cz", 4.6, 0.4),
        global_control=GlobalControl("global", 2, region=(-1, -1, 16, 1)),
    )


def _layout(device: Device, *sites: str) -> AtomLayout:
    ids = tuple(7 + 3 * index for index in range(len(sites)))
    return AtomLayout(
        tuple(
            AtomState(atom, device.site(site).position, SLMHolder(site))
            for atom, site in zip(ids, sites, strict=True)
        ),
        qubit_to_atom=ids,
    )


def _dag(qubits: tuple[int, ...], count: int) -> PhysicalDAG:
    return PhysicalDAG(
        count, 0, (PhysicalOperation(0, "global_ry", qubits, (), (0.25,)),), ()
    )


@pytest.mark.parametrize(
    "transport", [PairTransport("transport"), SequentialTransport("transport")]
)
def test_cz_retention_then_storage_global_rotation_has_complete_semantics(
    transport: PairTransport | SequentialTransport,
) -> None:
    device = _device()
    entry = _layout(device, "s0", "s1")
    dag = PhysicalDAG(
        2,
        0,
        (
            PhysicalOperation(0, "cz", (0, 1), (), ()),
            PhysicalOperation(1, "global_ry", (0, 1), (), (0.25,)),
        ),
        ((0, 1),),
    )

    result = compile_physical(
        dag,
        device,
        entry_state=entry,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), transport
        ),
    )

    report = validate_result(result, dag=dag, device=device, entry_state=entry)
    assert report["ok"], report
    current = entry
    for step in result.program.steps:
        for action in step.actions:
            if isinstance(action, GlobalRY):
                assert action.coverage == Region(-1, -1, 16, 1)
                assert all(
                    action.coverage.contains(atom.position) for atom in current.atoms
                )
        current = apply_step(current, step, device)
    assert current == result.exit_state


def test_all_region_global_rotation_keeps_already_legal_atoms() -> None:
    device = replace(_device(), global_control=GlobalControl("global", 2))
    layout = _layout(device, "g0", "s0", "outside")

    plan = PairPlacer("transport").place(
        _dag((0, 1, 2), 3), GateLayer((0,)), layout, device
    )

    assert plan.execution_layout is layout
    assert plan.exit_layout is layout


def test_subset_clears_spectators_and_reuses_vacated_sites() -> None:
    device = _device()
    entry = _layout(device, "g0", "s0", "outside")
    dag = _dag((0,), 3)

    plan = PairPlacer("transport").place(dag, GateLayer((0,)), entry, device)

    assert plan.execution_layout.atom(7).holder == SLMHolder("s1")
    assert plan.execution_layout.atom(10).holder == SLMHolder("g0")
    assert plan.execution_layout.atom(13) == entry.atom(13)
    result = compile_physical(
        dag,
        device,
        entry_state=entry,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), SequentialTransport("transport")
        ),
    )
    report = validate_result(result, dag=dag, device=device, entry_state=entry)
    assert report["ok"], report


def test_relocation_keeps_legal_participant_at_its_existing_site() -> None:
    device = _device()
    entry = _layout(device, "s1", "g0", "g1")

    plan = PairPlacer("transport").place(
        _dag((0, 1, 2), 3), GateLayer((0,)), entry, device
    )

    assert plan.execution_layout.atom(7) == entry.atom(7)
    assert plan.execution_layout.atom(10).holder == SLMHolder("s0")
    assert plan.execution_layout.atom(13).holder == SLMHolder("s2")
    assert len({atom.position for atom in plan.execution_layout.atoms}) == 3


def test_site_candidates_obey_rotate_zone_and_workspace() -> None:
    base = _device()
    device = replace(
        base,
        zones=(Zone("storage", Region(4, -1, 16, 1), ("rotate",)), *base.zones[1:]),
        aods=(replace(base.aods[0], workspace=Region(-5, -5, 6, 110)),),
    )
    entry = _layout(device, "g0")

    plan = PairPlacer("transport").place(_dag((0,), 1), GateLayer((0,)), entry, device)

    assert plan.execution_layout.atom(7).holder == SLMHolder("s1")


def test_unsupported_aod_holder_can_transfer_into_allowed_slm() -> None:
    device = replace(
        _device(), global_control=GlobalControl("global", 2, ("slm",), (-1, -1, 16, 1))
    )
    entry = AtomLayout(
        (AtomState(7, Position(0, 0), AODHolder("transport", 0, 0)),),
        (AODGrid("transport", (AODLine(0, 0),), (AODLine(0, 0),)),),
        (7,),
    )
    dag = _dag((0,), 1)

    result = compile_physical(
        dag,
        device,
        entry_state=entry,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
        ),
    )

    assert result.exit_state.atom(7).holder == SLMHolder("s0")
    report = validate_result(result, dag=dag, device=device, entry_state=entry)
    assert report["ok"], report


def test_no_free_legal_destination_reports_placement_failure() -> None:
    base = _device()
    device = replace(base, slm_sites=(base.site("s0"), base.site("g0")))
    entry = _layout(device, "g0", "s0")

    with pytest.raises(ValueError, match="Global RY.*destination"):
        PairPlacer("transport").place(_dag((0,), 2), GateLayer((0,)), entry, device)


def test_missing_allowed_slm_holder_does_not_widen_coverage() -> None:
    device = replace(
        _device(), global_control=GlobalControl("global", 2, ("aod",), (-1, -1, 16, 1))
    )
    entry = _layout(device, "g0")

    with pytest.raises(ValueError, match="Global RY.*destination"):
        PairPlacer("transport").place(_dag((0,), 1), GateLayer((0,)), entry, device)
