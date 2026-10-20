"""Build NADiT inputs from the same integer-coordinate C++ fixtures."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

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
from nadit_physical_compiler.transport.staging_geometry import (
    SiteKind,
    SiteStagingGeometry,
    StagingGeometry,
)

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class ReferenceCase:
    """One upstream batch with identical atom IDs and explicit SLM geometry."""

    name: str
    device: Device
    geometry: StagingGeometry
    moves: tuple[MoveRequest, ...]
    entry: AtomLayout
    target: AtomLayout


def load_cases(path: Path = HERE / "fixtures.json") -> tuple[ReferenceCase, ...]:
    """Convert fixture nanometers to micrometers without changing lattice bounds.

    Args:
        path: Shared JSON fixture used by the C++ adapter.

    Returns:
        Original-order batches with all storage/left/right SLM sites represented.
        Moving atom IDs precede stationary atom IDs, as in the C++ input.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    scale = data["nm_per_um"]
    slms = data["slms"]
    separation = (slms[2]["origin"][0] - slms[1]["origin"][0]) / scale
    sites: list[SLMSite] = []
    descriptions: dict[str, SiteStagingGeometry] = {}
    for index, slm in enumerate(slms):
        origin = Position(slm["origin"][0] / scale, slm["origin"][1] / scale)
        pitch = Position(slm["pitch"][0] / scale, slm["pitch"][1] / scale)
        kind = cast(SiteKind, slm["kind"])
        zone = "storage" if kind == "storage" else "entanglement"
        array = slm_grid(f"slm{index}", origin, slm["rows"], slm["columns"], pitch)
        description = SiteStagingGeometry(
            kind,
            pitch.x,
            pitch.y,
            origin.x,
            origin.x + slm["columns"] * pitch.x,
            origin.y,
            origin.y + slm["rows"] * pitch.y,
            0 if kind == "storage" else separation,
            zone,
        )
        sites.extend(array)
        descriptions.update((site.id, description) for site in array)
    zones = tuple(
        Zone(
            zone,
            _bounds(
                tuple(site for site in sites if descriptions[site.id].zone_id == zone)
            ),
            ("load", "store") if zone == "storage" else ("load", "store", "cz"),
        )
        for zone in ("storage", "entanglement")
    )
    arrays = tuple(descriptions.values())
    margin_x = max(item.pitch_x for item in arrays)
    margin_y = max(item.pitch_y for item in arrays)
    workspace = Region(
        min(item.min_x for item in arrays) - margin_x,
        min(item.min_y for item in arrays) - margin_y,
        max(item.max_x for item in arrays) + margin_x,
        max(item.max_y for item in arrays) + margin_y,
    )
    device = Device(
        "pinned-reference",
        zones,
        tuple(sites),
        (AOD("transport", workspace, 1),),
        Timing(10, 10, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )
    geometry = StagingGeometry(descriptions)
    cases = []
    for case in data["cases"]:
        source_sites = case["source_sites"] + case["stationary_sites"]
        target_sites = case["target_sites"] + case["stationary_sites"]
        entry = _layout(device, source_sites)
        target = _layout(device, target_sites)
        moves = tuple(
            MoveRequest(atom, entry.atoms[atom].position, target.atoms[atom].position)
            for atom in range(len(case["source_sites"]))
        )
        cases.append(
            ReferenceCase(case["name"], device, geometry, moves, entry, target)
        )
    return tuple(cases)


def _bounds(sites: tuple[SLMSite, ...]) -> Region:
    return Region(
        min(site.position.x for site in sites),
        min(site.position.y for site in sites),
        max(site.position.x for site in sites),
        max(site.position.y for site in sites),
    )


def _layout(device: Device, references: list[list[int]]) -> AtomLayout:
    atoms = []
    for atom_id, (slm, row, column) in enumerate(references):
        site = device.site(f"slm{slm}_r{row}_c{column}")
        atoms.append(AtomState(atom_id, site.position, SLMHolder(site.id)))
    return AtomLayout(tuple(atoms), qubit_to_atom=tuple(atom.id for atom in atoms))
