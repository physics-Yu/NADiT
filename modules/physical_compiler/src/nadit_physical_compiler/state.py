"""Atom positions, trap ownership, and active AOD line geometry."""

from dataclasses import dataclass
from itertools import pairwise
from math import floor, isclose, isfinite

from .device import Device, Position

COORDINATE_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class AODLine:
    """An active line with a stable ID and a coordinate in micrometres.

    IDs need not follow spatial order. Invalid IDs or nonfinite coordinates
    raise ValueError.
    """

    id: int
    coordinate: float

    def __post_init__(self) -> None:
        if self.id < 0 or not isfinite(self.coordinate):
            raise ValueError("AOD line IDs must be nonnegative and coordinates finite.")


@dataclass(frozen=True, slots=True)
class AODGrid:
    """Active AOD rows and columns, including lines with no occupied traps.

    Rows carry y coordinates and columns carry x coordinates. Empty device IDs
    or repeated line IDs within an axis raise ValueError.
    """

    aod_id: str
    rows: tuple[AODLine, ...] = ()
    columns: tuple[AODLine, ...] = ()

    def __post_init__(self) -> None:
        if not self.aod_id:
            raise ValueError("An AOD grid requires a device ID.")
        for name, lines in (("row", self.rows), ("column", self.columns)):
            if len({line.id for line in lines}) != len(lines):
                raise ValueError(f"AOD {self.aod_id!r} has duplicate {name} IDs.")

    def position(self, row: int, column: int) -> Position:
        """Return the intersection position; raise KeyError for missing lines."""
        y = _line_coordinate(self.rows, row, "row")
        x = _line_coordinate(self.columns, column, "column")
        return Position(x, y)


@dataclass(frozen=True, slots=True)
class SLMHolder:
    """Identify a fixed SLM site; an empty site ID raises ValueError."""

    site_id: str

    def __post_init__(self) -> None:
        if not self.site_id:
            raise ValueError("An SLM holder requires a site ID.")


@dataclass(frozen=True, slots=True)
class AODHolder:
    """Identify an AOD intersection by device and stable row/column IDs.

    An empty device ID or negative line ID raises ValueError.
    """

    aod_id: str
    row: int
    column: int

    def __post_init__(self) -> None:
        if not self.aod_id or self.row < 0 or self.column < 0:
            raise ValueError(
                "An AOD holder requires a device and nonnegative line IDs."
            )


@dataclass(frozen=True, slots=True)
class AtomState:
    """One atom's stable identity, position, and current trap holder.

    A negative ID raises ValueError. Device-dependent binding is checked by
    validate_layout.
    """

    id: int
    position: Position
    holder: SLMHolder | AODHolder

    def __post_init__(self) -> None:
        if self.id < 0:
            raise ValueError("Atom IDs must be nonnegative.")


@dataclass(frozen=True, slots=True)
class AtomLayout:
    """A planned atom arrangement and optional physical-qubit binding.

    qubit_to_atom maps each physical-qubit index to an atom ID; unbound spare
    atoms are permitted. Duplicate IDs, repeated bindings, and references to
    missing atoms raise ValueError.
    """

    atoms: tuple[AtomState, ...]
    aod_grids: tuple[AODGrid, ...] = ()
    qubit_to_atom: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        atom_ids = {atom.id for atom in self.atoms}
        if len(atom_ids) != len(self.atoms):
            raise ValueError("A layout has duplicate atom IDs.")
        if len({grid.aod_id for grid in self.aod_grids}) != len(self.aod_grids):
            raise ValueError("A layout has duplicate AOD grid IDs.")
        mapped_ids = set(self.qubit_to_atom)
        if len(mapped_ids) != len(self.qubit_to_atom) or not mapped_ids <= atom_ids:
            raise ValueError(
                "The qubit mapping must reference distinct existing atoms."
            )

    def atom(self, atom_id: int) -> AtomState:
        """Return the named atom; raise KeyError when its ID is absent."""
        for atom in self.atoms:
            if atom.id == atom_id:
                return atom
        raise KeyError(f"Unknown atom ID {atom_id}.")

    def grid(self, aod_id: str) -> AODGrid:
        """Return active lines, or an empty grid if this AOD has none recorded."""
        for grid in self.aod_grids:
            if grid.aod_id == aod_id:
                return grid
        return AODGrid(aod_id)


def validate_grid(device: Device, grid: AODGrid) -> None:
    """Check active lines against AOD capacity, spacing, and workspace.

    Args:
        device: Hardware description containing the named AOD.
        grid: Active lines, including vacant rows and columns.

    Raises:
        ValueError: If the AOD is unknown or the grid violates its capabilities.
    """
    try:
        aod = device.aod(grid.aod_id)
    except KeyError as exc:
        raise ValueError(f"Unknown AOD {grid.aod_id!r}.") from exc
    axes = (
        ("row", grid.rows, aod.max_rows, aod.workspace.y_min, aod.workspace.y_max),
        (
            "column",
            grid.columns,
            aod.max_columns,
            aod.workspace.x_min,
            aod.workspace.x_max,
        ),
    )
    for name, lines, capacity, lower, upper in axes:
        if capacity is not None and len(lines) > capacity:
            raise ValueError(f"AOD {grid.aod_id!r} exceeds its {name} capacity.")
        coordinates = sorted(line.coordinate for line in lines)
        if any(
            value < lower - COORDINATE_TOLERANCE or value > upper + COORDINATE_TOLERANCE
            for value in coordinates
        ):
            raise ValueError(f"AOD {grid.aod_id!r} has a {name} outside its workspace.")
        if any(
            right - left < aod.min_spacing - COORDINATE_TOLERANCE
            or _same_coordinate(left, right)
            for left, right in pairwise(coordinates)
        ):
            raise ValueError(f"AOD {grid.aod_id!r} violates minimum {name} spacing.")


def validate_layout(device: Device, layout: AtomLayout) -> None:
    """Validate an initial arrangement against its device.

    Args:
        device: SLM positions and AOD capabilities.
        layout: Atom holders, positions, active lines, and optional qubit binding.

    Raises:
        ValueError: For invalid trap references, inconsistent positions, occupied
            traps or positions, or active AOD intersections over SLM-held atoms.
    """
    for grid in layout.aod_grids:
        validate_grid(device, grid)
    holders: set[SLMHolder | AODHolder] = set()
    positions: dict[tuple[int, int], list[Position]] = {}
    for atom in layout.atoms:
        if atom.holder in holders:
            raise ValueError(f"Atom {atom.id} uses an already occupied trap.")
        holders.add(atom.holder)
        expected = _holder_position(device, layout, atom)
        if not _same_position(atom.position, expected):
            raise ValueError(f"Atom {atom.id} position does not match its holder.")
        _register_position(atom, positions)
        if isinstance(atom.holder, SLMHolder):
            for grid in layout.aod_grids:
                if any(
                    _same_coordinate(line.coordinate, atom.position.y)
                    for line in grid.rows
                ) and any(
                    _same_coordinate(line.coordinate, atom.position.x)
                    for line in grid.columns
                ):
                    raise ValueError(
                        f"SLM-held atom {atom.id} lies on an active intersection "
                        f"of AOD {grid.aod_id!r}."
                    )


def _line_coordinate(lines: tuple[AODLine, ...], line_id: int, axis: str) -> float:
    for line in lines:
        if line.id == line_id:
            return line.coordinate
    raise KeyError(f"Unknown AOD {axis} ID {line_id}.")


def _holder_position(device: Device, layout: AtomLayout, atom: AtomState) -> Position:
    try:
        if isinstance(atom.holder, SLMHolder):
            return device.site(atom.holder.site_id).position
        device.aod(atom.holder.aod_id)
        return layout.grid(atom.holder.aod_id).position(
            atom.holder.row, atom.holder.column
        )
    except KeyError as exc:
        raise ValueError(
            f"Atom {atom.id} has an invalid holder {atom.holder!r}: {exc}"
        ) from exc


def _same_coordinate(first: float, second: float) -> bool:
    return isclose(first, second, rel_tol=0, abs_tol=COORDINATE_TOLERANCE)


def _same_position(first: Position, second: Position) -> bool:
    return _same_coordinate(first.x, second.x) and _same_coordinate(first.y, second.y)


def _register_position(
    atom: AtomState, positions: dict[tuple[int, int], list[Position]]
) -> None:
    cell_x = floor(atom.position.x / COORDINATE_TOLERANCE)
    cell_y = floor(atom.position.y / COORDINATE_TOLERANCE)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if any(
                _same_position(atom.position, position)
                for position in positions.get((cell_x + dx, cell_y + dy), ())
            ):
                raise ValueError(f"Atom {atom.id} shares an occupied position.")
    positions.setdefault((cell_x, cell_y), []).append(atom.position)
