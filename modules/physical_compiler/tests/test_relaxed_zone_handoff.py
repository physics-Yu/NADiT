"""Gate-zone offsets use the pair separation of the corresponding endpoint."""

import pytest

from nadit_physical_compiler.atom_ir import AtomProgram, Load, Move, Store
from nadit_physical_compiler.capabilities import CZCapability
from nadit_physical_compiler.contracts import Transporter
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
    slm_grid,
)
from nadit_physical_compiler.motion import check_load, check_move, check_store
from nadit_physical_compiler.replay import apply_step, replay
from nadit_physical_compiler.state import (
    AODHolder,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_layout,
)
from nadit_physical_compiler.transport import (
    RelaxedTransport,
    StagedStrictTransport,
    StrictTransport,
)
from nadit_physical_compiler.transport.staged_policy import staging_regions
from nadit_physical_compiler.transport.staging_geometry import (
    SiteKind,
    SiteStagingGeometry,
    StagingGeometry,
)


def _different_gate_zones() -> tuple[Device, StagingGeometry, AtomLayout, AtomLayout]:
    """Keep each zone individually regular while changing its physical pair gap."""
    sites: list[SLMSite] = []
    descriptions: dict[str, SiteStagingGeometry] = {}
    for zone, y, separation in (("source", 0, 1.5), ("target", 120, 5)):
        kinds: tuple[tuple[SiteKind, float], ...] = (
            ("left", 0),
            ("right", separation),
        )
        for kind, x in kinds:
            array = slm_grid(f"{zone}_{kind}", Position(x, y), 1, 3, Position(12, 10))
            sites.extend(array)
            description = SiteStagingGeometry(
                kind, 12, 10, x, x + 36, y, y + 10, separation, zone
            )
            descriptions.update((site.id, description) for site in array)
    device = Device(
        "different-gate-zone-pair-gaps",
        (
            Zone("source", Region(0, 0, 30, 0), ("cz",)),
            Zone("target", Region(0, 120, 30, 120), ("cz",)),
        ),
        tuple(sites),
        (AOD("aod", Region(-30, -30, 60, 160), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 5.1, 1),
    )

    def layout(site_ids: tuple[str, ...]) -> AtomLayout:
        return AtomLayout(
            tuple(
                AtomState(index, device.site(site_id).position, SLMHolder(site_id))
                for index, site_id in enumerate(site_ids)
            ),
            qubit_to_atom=(0, 1, 2),
        )

    entry = layout(("source_left_r0_c0", "source_left_r0_c1", "target_right_r0_c0"))
    target = layout(("target_left_r0_c2", "target_left_r0_c1", "target_right_r0_c0"))
    return device, StagingGeometry(descriptions), entry, target


@pytest.mark.parametrize("relaxed", [False, True])
def test_gate_zone_gap_change_uses_legal_endpoint_offsets(relaxed: bool) -> None:
    device, geometry, entry, target = _different_gate_zones()
    validate_layout(device, entry)
    validate_layout(device, target)
    assert staging_regions(device, device.aod("aod"), geometry) == {
        "source",
        "target",
    }
    transporter: Transporter = (
        RelaxedTransport("aod", geometry, prefer_split=0)
        if relaxed
        else StagedStrictTransport("aod", geometry)
    )
    fragment = transporter.construct(entry, target, device)
    assert fragment != StrictTransport("aod").construct(entry, target, device)

    # Using source separation 1.5 in target pitch 12 gives an offset of 3.5.
    # A reversed column then parks at 12 - 2 * 3.5 = 5, on stationary atom 2.
    current = entry
    peak = 0
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
        assert current.atom(2) == entry.atom(2)
        peak = max(
            peak,
            sum(isinstance(atom.holder, AODHolder) for atom in current.atoms),
        )

    assert peak == (2 if relaxed else 1)
    assert current == fragment.exit_layout == target
    assert replay(AtomProgram(fragment.steps), entry, device) == target
