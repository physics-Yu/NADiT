"""One shared account of atom exposure, derived from completed action timelines."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from math import hypot
from typing import Any

from .atom_ir import (
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
    Wait,
)
from .coverage import RegionSet
from .device import Device, Region, loads_device
from .replay import apply_step
from .serialization import decode_layout, decode_program
from .state import AtomState, SLMHolder


@dataclass(frozen=True, slots=True)
class Interval:
    """An atom interval in microseconds, with a single holder/role category."""

    start_us: float
    end_us: float
    kind: str
    holder: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Action exposure including target semantics and actual movement endpoints."""

    kind: str
    start_us: float
    duration_us: float
    atoms: tuple[int, ...]
    starts: tuple[tuple[float, float], ...]
    ends: tuple[tuple[float, float], ...]
    pairs: tuple[tuple[int, int], ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    coverage: dict[str, Any] | None = None
    area_um2: float | None = None
    step_index: int = -1
    action_index: int = -1
    source_ids: tuple[int, ...] = ()
    measurement_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PerAtomActivity:
    """Physical busy intervals and complementary idle, plus event totals."""

    atom_id: int
    busy: tuple[Interval, ...]
    idle: tuple[Interval, ...]
    busy_us: float
    idle_us: float
    idle_by_holder_role: dict[str, float]
    loads: int
    stores: int
    transfers: int
    move_count: int
    move_distance_um: float
    cz_count: int
    rotation_count: int
    measure_count: int
    reset_count: int


@dataclass(frozen=True, slots=True)
class AtomActivity:
    """Model-independent activity shared by every cost model."""

    duration_us: float
    per_atom: tuple[PerAtomActivity, ...]
    events: tuple[ActivityEvent, ...]

    def document(self) -> dict[str, Any]:
        """Return JSON-ready activity including all pulse illumination geometry."""
        return asdict(self)


def _context(atom: AtomState, device: Device) -> tuple[str, tuple[str, ...]]:
    holder = "slm" if isinstance(atom.holder, SLMHolder) else "aod"
    roles = tuple(
        sorted({zone.role or zone.id for zone in device.zones_at(atom.position)})
    )
    return holder, roles or ("unclassified",)


def _area(regions: tuple[Region, ...]) -> float:
    xs = sorted({x for region in regions for x in (region.x_min, region.x_max)})
    area = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        spans = sorted(
            (r.y_min, r.y_max) for r in regions if r.x_min < right and r.x_max > left
        )
        length = 0.0
        end = float("-inf")
        for low, high in spans:
            length += max(0.0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * length
    return area


def derive_activity(document: Mapping[str, Any]) -> AtomActivity:
    """Replay a saved v2 result into exposure statistics; no compilation or sampling.

    Cost admission validates the document before this derivation. WAIT is idle,
    including pending feedback beyond the last step. Short parallel actions
    finish at their own end times, so tail idle uses their new holder/location.
    """
    import json

    program = decode_program(document["program"])
    device = loads_device(json.dumps(document["device"]))
    layout = decode_layout(document["entry_state"])
    duration = max(
        program.duration_us,
        max(
            (item["available_at_us"] for item in document["measurements"]), default=0.0
        ),
    )
    busy: dict[int, list[Interval]] = {atom.id: [] for atom in layout.atoms}
    idle: dict[int, list[Interval]] = {atom.id: [] for atom in layout.atoms}
    cursors = {atom.id: 0.0 for atom in layout.atoms}
    events: list[ActivityEvent] = []
    by_atom: dict[int, list[tuple[ActivityEvent, int]]] = {
        atom.id: [] for atom in layout.atoms
    }
    for step_index, (start, step) in enumerate(program.timeline()):
        after = apply_step(layout, step, device)
        before_atoms = {atom.id: atom for atom in layout.atoms}
        after_atoms = {atom.id: atom for atom in after.atoms}
        for action_index, action in enumerate(step.actions):
            parameters: dict[str, Any] = {}
            coverage: dict[str, Any] | None = None
            area = None
            pairs: tuple[tuple[int, int], ...] = ()
            if isinstance(action, Load | Store):
                ids = tuple(binding[0] for binding in action.bindings)
            elif isinstance(action, Move):
                ids = tuple(atom for atom, _ in action.targets)
            elif isinstance(action, Measure):
                ids = tuple(atom for atom, _ in action.results)
            elif isinstance(action, Rotation | LocalU | Reset):
                ids = action.atoms
                if isinstance(action, Rotation):
                    parameters = {"axis": action.axis, "angle": action.angle}
                elif isinstance(action, LocalU):
                    parameters = {
                        "theta": action.theta,
                        "phi": action.phi,
                        "lam": action.lam,
                    }
            elif isinstance(action, CZPulse | GlobalRY):
                illumination = action.coverage
                if isinstance(illumination, tuple):
                    ids = illumination
                    coverage = {"atoms": list(ids)}
                else:
                    regions = (
                        illumination.regions
                        if isinstance(illumination, RegionSet)
                        else (illumination,)
                    )
                    ids = tuple(
                        atom.id
                        for atom in layout.atoms
                        if illumination.contains(atom.position)
                    )
                    coverage = {"regions": [asdict(region) for region in regions]}
                    area = _area(regions)
                if isinstance(action, CZPulse):
                    pairs = tuple(
                        tuple(
                            layout.qubit_to_atom[q]
                            for q in document["dag"]["nodes"][index]["qubits"]
                        )
                        for index in action.source_ids
                    )
                else:
                    parameters = {"axis": "Y", "angle": action.angle}
            else:
                ids = ()
            kind = type(action).__name__
            event = ActivityEvent(
                kind,
                start,
                action.duration_us,
                ids,
                tuple(
                    (before_atoms[a].position.x, before_atoms[a].position.y)
                    for a in ids
                ),
                tuple(
                    (after_atoms[a].position.x, after_atoms[a].position.y) for a in ids
                ),
                pairs,
                parameters,
                coverage,
                area,
                step_index,
                action_index,
                action.source_ids,
                tuple(ref.measurement_id for _, ref in action.results)
                if isinstance(action, Measure)
                else (),
            )
            events.append(event)
            for index, atom_id in enumerate(ids):
                by_atom[atom_id].append((event, index))
            if not isinstance(action, Wait):
                for atom_id in ids:
                    holder, roles = _context(before_atoms[atom_id], device)
                    if cursors[atom_id] < start:
                        idle[atom_id].append(
                            Interval(cursors[atom_id], start, "idle", holder, roles)
                        )
                    busy[atom_id].append(
                        Interval(start, start + action.duration_us, kind, holder, roles)
                    )
                    cursors[atom_id] = max(cursors[atom_id], start + action.duration_us)
        layout = after
    for atom in layout.atoms:
        if duration > cursors[atom.id]:
            holder, roles = _context(atom, device)
            idle[atom.id].append(
                Interval(cursors[atom.id], duration, "idle", holder, roles)
            )
    return AtomActivity(
        duration,
        tuple(
            _atom_totals(atom, busy[atom], idle[atom], by_atom[atom]) for atom in busy
        ),
        tuple(events),
    )


def _atom_totals(
    atom: int,
    busy: list[Interval],
    idle: list[Interval],
    events: list[tuple[ActivityEvent, int]],
) -> PerAtomActivity:
    counts: dict[str, int] = {}
    distance = 0.0
    for event, index in events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
        if event.kind == "Move":
            source, target = event.starts[index], event.ends[index]
            distance += hypot(target[0] - source[0], target[1] - source[1])
    grouped: dict[str, float] = {}
    for interval in idle:
        key = interval.holder + ":" + "+".join(interval.roles)
        grouped[key] = grouped.get(key, 0) + interval.end_us - interval.start_us
    busy_us = 0.0
    end = 0.0
    for interval in busy:
        busy_us += max(0.0, interval.end_us - max(end, interval.start_us))
        end = max(end, interval.end_us)
    return PerAtomActivity(
        atom,
        tuple(busy),
        tuple(idle),
        busy_us,
        sum(grouped.values()),
        grouped,
        counts.get("Load", 0),
        counts.get("Store", 0),
        counts.get("Load", 0) + counts.get("Store", 0),
        counts.get("Move", 0),
        distance,
        counts.get("CZPulse", 0),
        sum(counts.get(k, 0) for k in ("Rotation", "LocalU", "GlobalRY")),
        counts.get("Measure", 0),
        counts.get("Reset", 0),
    )
