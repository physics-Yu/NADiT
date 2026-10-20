"""Explicit subarrays must share the lattice assumed by staged construction."""

from nadit_physical_compiler.atom_ir import AtomProgram, Load, Move, Store
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    Timing,
    Zone,
    slm_grid,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.state import (
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.transport.staged import StagedStrictTransport
from nadit_physical_compiler.transport.staged_policy import staging_regions
from nadit_physical_compiler.transport.staging_geometry import (
    SiteStagingGeometry,
    StagingGeometry,
)
from nadit_physical_compiler.transport.strict import StrictTransport


def _heterogeneous_zone() -> tuple[Device, StagingGeometry, AtomLayout, AtomLayout]:
    """Two valid target subarrays have different pitches within the same zone."""
    source = slm_grid("s", Position(-100, 0), 2, 3, Position(50, 5))
    target_a = slm_grid("a", Position(0, 100), 2, 1, Position(8, 8))
    target_b = slm_grid("b", Position(20, 100), 2, 3, Position(2, 8))
    device = Device(
        "heterogeneous-subarrays",
        (
            Zone("source", Region(-100, 0, 0, 5), ("load", "store")),
            Zone("target", Region(0, 100, 24, 108), ("load", "store")),
        ),
        source + target_a + target_b,
        (AOD("aod", Region(-150, -10, 100, 130), 1),),
        Timing(1, 1, 1),
    )
    descriptions: dict[str, SiteStagingGeometry] = {}
    for sites, description in (
        (
            source,
            SiteStagingGeometry("storage", 50, 5, -100, 50, 0, 10, 0, "source"),
        ),
        (
            target_a,
            SiteStagingGeometry("storage", 8, 8, 0, 8, 100, 116, 0, "target"),
        ),
        (
            target_b,
            SiteStagingGeometry("storage", 2, 8, 20, 26, 100, 116, 0, "target"),
        ),
    ):
        descriptions.update((site.id, description) for site in sites)

    def layout(site_ids: tuple[str, ...]) -> AtomLayout:
        return AtomLayout(
            tuple(
                AtomState(index, device.site(site_id).position, SLMHolder(site_id))
                for index, site_id in enumerate(site_ids)
            ),
            qubit_to_atom=(0, 1, 2, 3),
        )

    entry = layout(("s_r0_c0", "s_r1_c1", "s_r1_c2", "b_r0_c2"))
    target = layout(("a_r0_c0", "b_r1_c0", "b_r1_c1", "b_r0_c2"))
    return device, StagingGeometry(descriptions), entry, target


def test_heterogeneous_zone_uses_legal_direct_transport_without_ghost_pickup() -> None:
    device, geometry, entry, target = _heterogeneous_zone()
    validate_layout(device, entry)
    validate_layout(device, target)

    # Using target_a's pitch for target_b parks a column at x=24 while y=100
    # is active, which would pick up the stationary atom at b_r0_c2.
    admitted = staging_regions(device, device.aod("aod"), geometry)
    assert "source" in admitted
    assert "target" not in admitted
    fragment = StagedStrictTransport("aod", geometry).construct(entry, target, device)
    assert fragment == StrictTransport("aod").construct(entry, target, device)

    current = entry
    for step in fragment.steps:
        assert len(step.actions) == 1
        action = step.actions[0]
        if isinstance(action, Load):
            result = check_load(
                device,
                current,
                action.grid,
                {atom: (row, column) for atom, row, column in action.bindings},
            )
        elif isinstance(action, Move):
            result = check_move(device, current, action.grid, dict(action.targets))
        else:
            assert isinstance(action, Store)
            result = check_store(device, current, action.grid, dict(action.bindings))
        assert result.ok, result
        current = apply_step(current, step, device)
        validate_layout(device, current)
        assert current.atom(3) == entry.atom(3)

    assert current == fragment.exit_layout == target
    assert replay(AtomProgram(fragment.steps), entry, device) == target
