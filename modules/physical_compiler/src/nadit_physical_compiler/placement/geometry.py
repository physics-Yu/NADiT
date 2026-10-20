"""SLM arrays and nearest-site queries adapted from MQT QMAP Architecture.cpp.

Reference: MQT QMAP cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d (MIT).
"""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from dataclasses import dataclass, field
from math import floor, hypot
from typing import Literal, Self

from ..device import Device, Position, SLMSite
from ..state import COORDINATE_TOLERANCE
from ..transport.staging_geometry import StagingGeometry
from .catalog import GatePairCatalog

type SiteGrid = tuple[tuple[SLMSite, ...], ...]


@dataclass(frozen=True, slots=True)
class SLMArray:
    """One rectangular SLM array with explicitly ordered rows and columns.

    Args:
        id: Array identity, independent of an operation-zone identity.
        kind: Storage array or one side of a paired entanglement array.
        sites: Sites in increasing y rows and increasing x columns.
        pitch: Lattice spacing, including otherwise unspecified singleton axes.
        partner_sites: Corresponding sites of the other entanglement array.
    """

    id: str
    kind: Literal["storage", "left", "right"]
    sites: SiteGrid
    pitch: Position
    partner_sites: SiteGrid = ()

    @property
    def n_rows(self) -> int:
        """Return the number of lattice rows."""
        return len(self.sites)

    @property
    def n_cols(self) -> int:
        """Return the number of lattice columns."""
        return len(self.sites[0])

    @property
    def origin(self) -> Position:
        """Return the first lattice site's coordinate."""
        return self.sites[0][0].position

    def site(self, row: int, column: int) -> SLMSite:
        """Return a lattice site; invalid indices raise IndexError."""
        return self.sites[row][column]

    def gate_pair(self, row: int, column: int) -> tuple[SLMSite, SLMSite]:
        """Return this array's site and its corresponding partner site."""
        return self.site(row, column), self.partner_sites[row][column]

    def project(self, position: Position) -> tuple[int, int]:
        """Project a coordinate onto this lattice, rounding half up and clamping."""
        origin = self.origin
        row = floor((position.y - origin.y) / self.pitch.y + 0.5)
        column = floor((position.x - origin.x) / self.pitch.x + 0.5)
        return min(max(row, 0), self.n_rows - 1), min(max(column, 0), self.n_cols - 1)


type SiteLocation = tuple[SLMArray, int, int]


def _paired_arrays(
    pairs: tuple[tuple[SLMSite, SLMSite], ...],
    name: str,
    spacing: float,
    staging: StagingGeometry | None,
) -> tuple[SLMArray, SLMArray]:
    ordered = tuple(
        tuple(sorted(pair, key=lambda site: (site.position.x, site.position.y)))
        for pair in pairs
    )
    separation = hypot(
        ordered[0][0].position.x - ordered[0][1].position.x,
        ordered[0][0].position.y - ordered[0][1].position.y,
    )
    pitch = Position(separation + 3 * spacing, 2 * spacing)
    if staging is not None and ordered[0][0].id in staging.sites:
        descriptor = staging.sites[ordered[0][0].id]
        pitch = Position(descriptor.pitch_x, descriptor.pitch_y)
    left = _array(f"{name}:left", "left", tuple(pair[0] for pair in ordered), pitch)
    right = _array(f"{name}:right", "right", tuple(pair[1] for pair in ordered), pitch)
    inferred = {
        frozenset((first.id, second.id))
        for left_row, right_row in zip(left.sites, right.sites, strict=True)
        for first, second in zip(left_row, right_row, strict=True)
    }
    if inferred != {frozenset((first.id, second.id)) for first, second in pairs}:
        raise ValueError(
            "Catalogue pairing does not follow common SLM row/column indices; "
            "provide explicit PlacementGeometry."
        )
    return (
        SLMArray(left.id, left.kind, left.sites, left.pitch, right.sites),
        SLMArray(right.id, right.kind, right.sites, right.pitch, left.sites),
    )


@dataclass(frozen=True, slots=True)
class PlacementGeometry:
    """Ordered storage and paired entanglement arrays with cached site queries.

    Args:
        storage_arrays: SLM declaration order used for initial filling and ties.
        entanglement_arrays: Zone order, then the two SLMs in each zone.

    Nearest-site queries select an SLM first and project onto that array second.
    Caches are populated on demand instead of materializing all storage pairs.
    """

    storage_arrays: tuple[SLMArray, ...]
    entanglement_arrays: tuple[tuple[SLMArray, SLMArray], ...]
    _locations: dict[str, SiteLocation] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _positions: dict[Position, SiteLocation] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _storage_order: dict[str, int] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _storage_cache: dict[Position, SiteLocation] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _gate_cache: dict[tuple[Position, Position], SiteLocation] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        self._storage_order.update(
            (array.id, index) for index, array in enumerate(self.storage_arrays)
        )
        arrays = self.storage_arrays + tuple(
            array for pair in self.entanglement_arrays for array in pair
        )
        for array in arrays:
            for row, sites in enumerate(array.sites):
                for column, site in enumerate(sites):
                    location = array, row, column
                    self._locations[site.id] = location
                    self._positions.setdefault(site.position, location)

    @classmethod
    def from_device(
        cls,
        device: Device,
        aod_id: str,
        catalog: GatePairCatalog,
        storage_zone_id: str | tuple[str, ...] = "storage",
        *,
        staging: StagingGeometry | None = None,
    ) -> Self:
        """Infer regular arrays from storage zones and the selected gate catalogue.

        Args:
            device: Fixed traps and zones.
            aod_id: AOD supplying singleton-axis pitch defaults.
            catalog: Paired entanglement sites; other sites in its zone are ignored.
            storage_zone_id: One or several storage zones, in declaration order.
            staging: Optional explicit lattice pitches for singleton axes.

        Returns:
            Storage arrays and one pair of arrays per catalogue region.

        Raises:
            ValueError: If flat sites do not describe rectangular regular arrays;
                provide explicit SLMArray records to specify their actual grouping.
            KeyError: If a requested zone or AOD does not exist.
        """
        spacing = device.aod(aod_id).min_spacing
        zones = {zone.id: zone for zone in device.zones}
        names = (
            (storage_zone_id,) if isinstance(storage_zone_id, str) else storage_zone_id
        )
        storage: list[SLMArray] = []
        assigned: set[str] = set()
        for name in names:
            sites = tuple(
                site
                for site in device.slm_sites
                if site.id not in assigned
                and zones[name].bounds.contains(site.position)
                and not catalog.coverage.contains(site.position)
            )
            if not sites:
                continue
            pitch = Position(2 * spacing, 2 * spacing)
            if staging is not None and sites and sites[0].id in staging.sites:
                descriptor = staging.sites[sites[0].id]
                pitch = Position(descriptor.pitch_x, descriptor.pitch_y)
            storage.append(_array(name, "storage", sites, pitch))
            assigned.update(site.id for site in sites)
        groups = catalog.region_pairs
        gates = tuple(
            _paired_arrays(
                pairs,
                "entanglement" if len(groups) == 1 else f"entanglement:{index}",
                spacing,
                staging,
            )
            for index, pairs in enumerate(groups)
        )
        return cls(tuple(storage), gates)

    def locate(self, site_id: str) -> SiteLocation:
        """Return array and lattice indices; unknown site IDs raise KeyError."""
        return self._locations[site_id]

    def site_location(self, position: Position) -> SiteLocation:
        """Return a fixed site's array and indices; unknown positions raise KeyError."""
        return self._positions[position]

    def nearest_storage(self, position: Position) -> SiteLocation:
        """Find the nearest storage rectangle, then round and clamp its site."""
        if position not in self._storage_cache:
            array = min(
                self.storage_arrays,
                key=lambda item: _rectangle_distance(position, item),
            )
            row, column = array.project(position)
            self._storage_cache[position] = array, row, column
        return self._storage_cache[position]

    def nearest_entanglement(self, first: Position, second: Position) -> SiteLocation:
        """Select a gate SLM by the source lower bound and project the first site.

        The source cache canonicalizes the input storage sites by SLM identity,
        row and column. Array declaration order supplies stable SLM identity here.
        """
        if self._storage_key(second) < self._storage_key(first):
            first, second = second, first
        key = first, second
        if key not in self._gate_cache:
            array = min(
                (array for pair in self.entanglement_arrays for array in pair),
                key=lambda item: _pair_rectangle_distance(first, second, item),
            )
            row, column = array.project(first)
            self._gate_cache[key] = array, row, column
        return self._gate_cache[key]

    def nearest_entanglement_distance(self, first: Position, second: Position) -> float:
        """Return the source nearest-site estimate: same storage row max, else sum."""
        array, row, column = self.nearest_entanglement(first, second)
        target = array.site(row, column).position
        distances = (
            hypot(first.x - target.x, first.y - target.y),
            hypot(second.x - target.x, second.y - target.y),
        )
        first_array, first_row, _ = self.site_location(first)
        second_array, second_row, _ = self.site_location(second)
        return (
            max(distances)
            if first_array.id == second_array.id and first_row == second_row
            else sum(distances)
        )

    def _storage_key(self, position: Position) -> tuple[int, int, int]:
        array, row, column = self.site_location(position)
        return self._storage_order[array.id], row, column


def _rectangle_distance(position: Position, array: SLMArray) -> float:
    origin = array.origin
    end = array.site(array.n_rows - 1, array.n_cols - 1).position
    return hypot(
        max(origin.x - position.x, position.x - end.x, 0),
        max(origin.y - position.y, position.y - end.y, 0),
    )


def _pair_axis_distance(first: float, second: float, low: float, high: float) -> float:
    distance = abs(first - second)
    if first < low and second < low:
        distance += 2 * (low - max(first, second))
    elif first > high and second > high:
        distance += 2 * (min(first, second) - high)
    return distance


def _pair_rectangle_distance(
    first: Position, second: Position, array: SLMArray
) -> float:
    origin = array.origin
    end = array.site(array.n_rows - 1, array.n_cols - 1).position
    # The source y branch omits the second-point side test and can underflow
    # size_t for straddling inputs. Use the same two-point test on both axes.
    return hypot(
        _pair_axis_distance(first.x, second.x, origin.x, end.x),
        _pair_axis_distance(first.y, second.y, origin.y, end.y),
    )


def _array(
    identifier: str,
    kind: Literal["storage", "left", "right"],
    sites: tuple[SLMSite, ...],
    fallback: Position,
) -> SLMArray:
    if not sites:
        raise ValueError(f"SLM array {identifier!r} has no fixed sites.")
    xs = tuple(sorted({site.position.x for site in sites}))
    ys = tuple(sorted({site.position.y for site in sites}))
    by_position = {site.position: site for site in sites}
    if len(by_position) != len(xs) * len(ys):
        raise ValueError(
            f"SLM array {identifier!r} is not a rectangular lattice; "
            "provide explicit PlacementGeometry."
        )
    pitch = Position(
        _pitch(xs, fallback.x, identifier),
        _pitch(ys, fallback.y, identifier),
    )
    grid = tuple(tuple(by_position[Position(x, y)] for x in xs) for y in ys)
    return SLMArray(identifier, kind, grid, pitch)


def _pitch(values: tuple[float, ...], fallback: float, identifier: str) -> float:
    if len(values) == 1:
        return fallback
    pitch = values[1] - values[0]
    if any(
        abs(right - left - pitch) > COORDINATE_TOLERANCE
        for left, right in zip(values, values[1:], strict=False)
    ):
        raise ValueError(
            f"SLM array {identifier!r} has nonuniform spacing; "
            "provide explicit PlacementGeometry."
        )
    return pitch
