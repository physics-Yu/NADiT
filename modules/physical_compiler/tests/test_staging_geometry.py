"""SLM geometry inference and basic staged atom-event construction."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.capabilities import CZCapability
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
from nadit_physical_compiler.state import AtomLayout, AtomState, SLMHolder
from nadit_physical_compiler.transport.rules import MoveRequest
from nadit_physical_compiler.transport.staging import generate_stages
from nadit_physical_compiler.transport.staging_geometry import (
    SiteStagingGeometry,
    StagingGeometry,
)


def _device() -> Device:
    return Device(
        "staging",
        (
            Zone("storage", Region(0, 0, 15, 15), ("load", "store")),
            Zone("gate", Region(0, 120, 18, 150), ("cz",)),
        ),
        slm_grid("storage", Position(0, 0), 4, 4, Position(5, 5))
        + slm_grid("left", Position(0, 120), 4, 3, Position(8, 10))
        + slm_grid("right", Position(2, 120), 4, 3, Position(8, 10)),
        (AOD("a", Region(-20, -20, 50, 180), 1),),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )


def test_regular_arrays_preserve_one_past_bounds_and_gate_subarray_origins() -> None:
    geometry = StagingGeometry.from_device(_device(), "a")

    assert geometry.sites["storage_r0_c0"] == SiteStagingGeometry(
        "storage", 5, 5, 0, 20, 0, 20, 0, "storage"
    )
    assert geometry.sites["left_r0_c0"] == SiteStagingGeometry(
        "left", 8, 10, 0, 24, 120, 160, 2, "gate"
    )
    assert geometry.sites["right_r0_c0"] == SiteStagingGeometry(
        "right", 8, 10, 2, 26, 120, 160, 2, "gate"
    )
    assert geometry.sites["left_r3_c2"] is geometry.sites["left_r0_c0"]


def test_single_axis_pitches_use_documented_minimum_spacing_fallbacks() -> None:
    device = replace(
        _device(),
        slm_sites=(
            SLMSite("single", Position(0, 0)),
            SLMSite("left", Position(0, 120)),
            SLMSite("right", Position(2, 120)),
        ),
    )
    geometry = StagingGeometry.from_device(device, "a")

    assert geometry.sites["single"].pitch_x == 2
    assert geometry.sites["single"].pitch_y == 2
    assert geometry.sites["left"].pitch_x == 5
    assert geometry.sites["left"].pitch_y == 2
    assert geometry.sites["right"].pair_separation == 2


def test_irregular_storage_is_omitted_without_losing_recognized_gate_sites() -> None:
    device = _device()
    device = replace(
        device,
        slm_sites=tuple(
            site for site in device.slm_sites if site.id != "storage_r3_c3"
        ),
    )
    geometry = StagingGeometry.from_device(device, "a")
    assert not any(site.startswith("storage") for site in geometry.sites)
    assert "left_r0_c0" in geometry.sites


def test_ambiguous_gate_pattern_is_omitted() -> None:
    device = _device()
    device = replace(
        device, slm_sites=device.slm_sites + (SLMSite("extra", Position(5, 120)),)
    )
    geometry = StagingGeometry.from_device(device, "a")
    assert "storage_r0_c0" in geometry.sites
    assert not any(
        site.startswith(("left", "right")) or site == "extra" for site in geometry.sites
    )


def test_explicit_geometry_can_replace_an_inferred_single_row_pitch() -> None:
    device = _device()
    inferred = StagingGeometry.from_device(device, "a")
    explicit = StagingGeometry(
        {
            site: replace(description, pitch_y=20, max_y=200)
            if description.zone_id == "gate"
            else description
            for site, description in inferred.sites.items()
        }
    )
    assert explicit.sites["left_r0_c0"].pitch_y == 20
    assert inferred.sites["left_r0_c0"].pitch_y == 10


@pytest.mark.parametrize(
    ("source_ids", "target_ids"),
    [
        (
            ("storage_r0_c0", "storage_r0_c1"),
            ("left_r0_c0", "right_r0_c0"),
        ),
        (
            ("storage_r0_c0", "storage_r1_c0", "storage_r2_c0"),
            ("left_r0_c0", "left_r1_c0", "left_r2_c0"),
        ),
    ],
)
def test_equal_masks_jointly_transfer_and_moves_only_report_changed_atoms(
    source_ids: tuple[str, ...], target_ids: tuple[str, ...]
) -> None:
    device = _device()
    geometry = StagingGeometry.from_device(device, "a")
    atoms = tuple(
        AtomState(index, device.site(site).position, SLMHolder(site))
        for index, site in enumerate(source_ids)
    )
    layout = AtomLayout(atoms, qubit_to_atom=tuple(range(len(atoms))))
    destinations = {
        index: AtomState(index, device.site(site).position, SLMHolder(site))
        for index, site in enumerate(target_ids)
    }
    moves = tuple(
        MoveRequest(atom.id, atom.position, destinations[atom.id].position)
        for atom in atoms
    )
    events = generate_stages(moves, layout, destinations, geometry)

    assert [event.kind for event in events] == ["load", "move", "move", "move", "store"]
    assert tuple(atom for atom, _ in events[0].positions) == tuple(range(len(atoms)))
    current: dict[int, Position] = {}
    for event in events:
        assert event.positions == tuple(sorted(event.positions))
        if event.kind == "load":
            current.update(event.positions)
        elif event.kind == "move":
            assert all(current[atom] != target for atom, target in event.positions)
            current.update(event.positions)
        else:
            assert all(
                current[atom] == target == destinations[atom].position
                for atom, target in event.positions
            )
            for atom, _ in event.positions:
                del current[atom]
    assert not current


def test_empty_batch_has_no_geometry_or_event_requirements() -> None:
    assert generate_stages((), AtomLayout(()), {}, StagingGeometry({})) == ()
