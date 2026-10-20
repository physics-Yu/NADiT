"""Device geometry and transport capabilities in micrometers and microseconds."""

import json
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Literal

from .capabilities import CZCapability, GlobalControl, LocalControl, ReadoutCapability


@dataclass(frozen=True, slots=True)
class Position:
    """A planar coordinate in micrometers.

    Args:
        x: Horizontal coordinate.
        y: Vertical coordinate.

    Raises:
        ValueError: If either coordinate is not finite.
    """

    x: float
    y: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise ValueError("Position coordinates must be finite.")


@dataclass(frozen=True, slots=True)
class Region:
    """An axis-aligned region, including its boundary.

    Args:
        x_min: Left boundary in micrometers.
        y_min: Bottom boundary in micrometers.
        x_max: Right boundary in micrometers.
        y_max: Top boundary in micrometers.

    Raises:
        ValueError: If bounds are nonfinite or reversed.
    """

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        if not all(
            isfinite(value)
            for value in (self.x_min, self.y_min, self.x_max, self.y_max)
        ):
            raise ValueError("Region bounds must be finite.")
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("Region minimum bounds must not exceed maximum bounds.")

    def contains(self, position: Position) -> bool:
        """Return whether a coordinate lies inside or on this region."""
        return (
            self.x_min <= position.x <= self.x_max
            and self.y_min <= position.y <= self.y_max
        )


@dataclass(frozen=True, slots=True)
class Zone:
    """A region and its allowed operation names.

    Args:
        id: Device-local zone identifier.
        bounds: Geometric extent, independent of atom holders.
        operations: Allowed operation names, such as ``cz`` or ``measure``.
    """

    id: str
    bounds: Region
    operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SLMSite:
    """A fixed SLM trap.

    Args:
        id: Device-local site identifier.
        position: Trap coordinate in micrometers.
    """

    id: str
    position: Position


@dataclass(frozen=True, slots=True)
class AOD:
    """A flexible, order-preserving row/column transport device.

    Args:
        id: Device-local AOD identifier.
        workspace: Allowed row/column intersection coordinates.
        min_spacing: Minimum separation of adjacent rows or columns.
        max_rows: Maximum active row count; None means no count limit.
        max_columns: Maximum active column count; None means no count limit.

    Raises:
        ValueError: If spacing is not finite and positive or a capacity
            is nonpositive.
    """

    id: str
    workspace: Region
    min_spacing: float
    max_rows: int | None = None
    max_columns: int | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.min_spacing) or self.min_spacing <= 0:
            raise ValueError(f"AOD {self.id} spacing must be finite and positive.")
        for name, capacity in (
            ("row", self.max_rows),
            ("column", self.max_columns),
        ):
            if capacity is not None and capacity <= 0:
                raise ValueError(f"AOD {self.id} {name} capacity must be positive.")


@dataclass(frozen=True, slots=True)
class Timing:
    """Transfer durations and a selectable distance-to-time function.

    Args:
        load_us: SLM-to-AOD transfer duration in microseconds.
        store_us: AOD-to-SLM transfer duration in microseconds.
        move_us_per_um: Linear-profile movement duration per micrometer.
        move_profile: Linear timing or the Search Smarter evaluation profile.

    Raises:
        ValueError: If a timing parameter is nonfinite or negative.
    """

    load_us: float
    store_us: float
    move_us_per_um: float
    move_profile: Literal["linear", "paper"] = "linear"

    def __post_init__(self) -> None:
        for name, value in (
            ("load_us", self.load_us),
            ("store_us", self.store_us),
            ("move_us_per_um", self.move_us_per_um),
        ):
            if not isfinite(value) or value < 0:
                raise ValueError(f"Timing {name} must be finite and nonnegative.")
        if self.move_profile not in ("linear", "paper"):
            raise ValueError(f"Unknown movement timing profile: {self.move_profile}.")

    def move_duration(self, distance: float) -> float:
        """Return movement time in microseconds for a distance in micrometers.

        Raises:
            ValueError: If distance is nonfinite or negative.
        """
        if not isfinite(distance) or distance < 0:
            raise ValueError("Movement distance must be finite and nonnegative.")
        if self.move_profile == "paper":
            if distance <= 110:
                return float(200 * (distance / 110) ** (1 / 3))
            return 200 + (distance - 110) / 1.1
        return self.move_us_per_um * distance


@dataclass(frozen=True, slots=True)
class Device:
    """Static geometry, transport, and operation capabilities used by a compiler.

    Args:
        name: Configuration name.
        zones: Operation regions; overlapping bounds are allowed.
        slm_sites: Fixed traps, with unique site IDs.
        aods: Transport devices, with unique AOD IDs.
        timing: Transfer and movement timing.
        cz: Optional CZ channel and interaction capability.
        readout: Optional readout channel and result timing.
        local: Optional addressed rotation and reset channel.
        global_control: Optional global single-qubit rotation channel.

    Raises:
        ValueError: If a zone, site, or AOD ID is empty or duplicated.
    """

    name: str
    zones: tuple[Zone, ...]
    slm_sites: tuple[SLMSite, ...]
    aods: tuple[AOD, ...]
    timing: Timing
    cz: CZCapability | None = None
    readout: ReadoutCapability | None = None
    local: LocalControl | None = None
    global_control: GlobalControl | None = None
    _sites_by_id: dict[str, SLMSite] = field(init=False, repr=False, compare=False)
    _aods_by_id: dict[str, AOD] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for label, entries in (
            ("zone", self.zones),
            ("SLM site", self.slm_sites),
            ("AOD", self.aods),
        ):
            ids = tuple(entry.id for entry in entries)
            if any(not identifier for identifier in ids):
                raise ValueError(f"Device {self.name} has an empty {label} ID.")
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {label} ID in device {self.name}.")
        object.__setattr__(
            self, "_sites_by_id", {site.id: site for site in self.slm_sites}
        )
        object.__setattr__(self, "_aods_by_id", {aod.id: aod for aod in self.aods})

    def site(self, id: str) -> SLMSite:
        """Return a fixed site by ID; raise KeyError when it is absent."""
        return self._sites_by_id[id]

    def aod(self, id: str) -> AOD:
        """Return an AOD by ID; raise KeyError when it is absent."""
        return self._aods_by_id[id]

    def zones_at(
        self, position: Position, operation: str | None = None
    ) -> tuple[Zone, ...]:
        """Return containing zones, optionally filtered by an allowed operation."""
        return tuple(
            zone
            for zone in self.zones
            if zone.bounds.contains(position)
            and (operation is None or operation in zone.operations)
        )


def slm_grid(
    prefix: str,
    origin: Position,
    rows: int,
    columns: int,
    pitch: Position,
) -> tuple[SLMSite, ...]:
    """Generate a rectangular set of fixed traps in row-major order.

    Args:
        prefix: Site ID prefix; IDs are ``{prefix}_r{row}_c{column}``.
        origin: Coordinate of row 0, column 0.
        rows: Number of rows.
        columns: Number of columns.
        pitch: Horizontal and vertical spacing in micrometers.

    Returns:
        Sites with zero-based row and column indices.

    Raises:
        ValueError: If row count, column count, or pitch is nonpositive.
    """
    if rows <= 0 or columns <= 0 or pitch.x <= 0 or pitch.y <= 0:
        raise ValueError("SLM grid counts and pitch must be positive.")
    return tuple(
        SLMSite(
            f"{prefix}_r{row}_c{column}",
            Position(origin.x + column * pitch.x, origin.y + row * pitch.y),
        )
        for row in range(rows)
        for column in range(columns)
    )


def loads_device(source: str) -> Device:
    """Load device geometry and optional operation capabilities from JSON.

    Args:
        source: JSON using the Device field names. Positions use ``x`` and
            ``y``; regions use ``x_min``, ``y_min``, ``x_max``, ``y_max``.
            Optional ``slm_grids`` entries use the arguments of slm_grid.
            Optional ``cz``, ``readout``, and ``local`` entries use the fields
            of the corresponding capability records.

    Returns:
        A device containing explicit sites followed by generated grid sites.

    Raises:
        ValueError: If JSON or device values are invalid.
        KeyError: If a required configuration field is missing.
        TypeError: If constructor fields do not match the data records.
    """
    data = json.loads(source)
    cz = data.get("cz")
    if cz is not None and "holder_pairs" in cz:
        cz["holder_pairs"] = tuple((pair[0], pair[1]) for pair in cz["holder_pairs"])
    readout = data.get("readout")
    local = data.get("local")
    global_control = data.get("global_control")
    if global_control is not None and global_control.get("region") is not None:
        global_control["region"] = tuple(global_control["region"])
    for capability in (readout, local, global_control):
        if capability is not None and "holders" in capability:
            capability["holders"] = tuple(capability["holders"])
    sites = [
        SLMSite(entry["id"], Position(**entry["position"]))
        for entry in data.get("slm_sites", [])
    ]
    for grid in data.get("slm_grids", []):
        sites.extend(
            slm_grid(
                grid["prefix"],
                Position(**grid["origin"]),
                grid["rows"],
                grid["columns"],
                Position(**grid["pitch"]),
            )
        )
    return Device(
        name=data["name"],
        zones=tuple(
            Zone(entry["id"], Region(**entry["bounds"]), tuple(entry["operations"]))
            for entry in data["zones"]
        ),
        slm_sites=tuple(sites),
        aods=tuple(
            AOD(
                id=entry["id"],
                workspace=Region(**entry["workspace"]),
                min_spacing=entry["min_spacing"],
                max_rows=entry.get("max_rows"),
                max_columns=entry.get("max_columns"),
            )
            for entry in data["aods"]
        ),
        timing=Timing(**data["timing"]),
        cz=CZCapability(**cz) if cz is not None else None,
        readout=ReadoutCapability(**readout) if readout is not None else None,
        local=LocalControl(**local) if local is not None else None,
        global_control=(
            GlobalControl(**global_control) if global_control is not None else None
        ),
    )


def load_device(path: str | Path) -> Device:
    """Read a UTF-8 JSON device file.

    Args:
        path: Configuration file path.

    Returns:
        The parsed device.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If JSON or device values are invalid.
        KeyError: If a required configuration field is missing.
        TypeError: If constructor fields do not match the data records.
    """
    return loads_device(Path(path).read_text(encoding="utf-8"))
