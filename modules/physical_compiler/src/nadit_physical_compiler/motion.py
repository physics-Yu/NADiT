"""Reference checks for AOD transport tests and explicit debugging.

Each check evaluates a concrete target grid against validated entry state.
The default compiler construction path does not call these full-state checks.
Coordinates describe endpoints; routing supplies intermediate movements.
"""

from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from math import inf, isclose
from typing import Literal

from .device import Device, Position
from .state import (
    COORDINATE_TOLERANCE,
    AODGrid,
    AODHolder,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
    validate_grid,
)


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    """A successful candidate or its first violated constraint.

    Args:
        rule: Stable rule name, or None for success.
        message: Description of the conflicting atoms, lines, or positions.
    """

    rule: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        """Return whether the candidate satisfies the checked constraints."""
        return self.rule is None


def check_move(
    device: Device,
    layout: AtomLayout,
    target_grid: AODGrid,
    targets: Mapping[int, Position],
) -> ConstraintResult:
    """Check a parallel move, including every atom carried by affected lines.

    Args:
        device: Device geometry and AOD motion capabilities.
        layout: Validated starting layout.
        target_grid: Final coordinates of all currently active lines.
        targets: Explicit atom destinations; omitted atoms must stay in place.

    Returns:
        Success or the first failed geometry, motion, occupancy, or target rule.
    """
    result = _check_grid_change(device, layout, target_grid, "move")
    if not result.ok:
        return result
    atoms = {atom.id: atom for atom in layout.atoms}
    result = _check_atoms(atoms, targets, target_grid.aod_id)
    if not result.ok:
        return result
    stationary = sorted(
        (atom.position.x, atom.position.y)
        for atom in layout.atoms
        if not _carried_by(atom, target_grid.aod_id)
    )
    rows, columns = _coordinates(target_grid)
    for atom in layout.atoms:
        if not _carried_by(atom, target_grid.aod_id):
            continue
        holder = atom.holder
        assert isinstance(holder, AODHolder)
        destination = Position(columns[holder.column], rows[holder.row])
        if atom.id not in targets and not _same_position(destination, atom.position):
            return ConstraintResult(
                "omitted_atom", f"Active lines also move atom {atom.id}."
            )
        if atom.id in targets and not _same_position(destination, targets[atom.id]):
            return ConstraintResult(
                "target_mismatch",
                f"Atom {atom.id} does not reach its requested target.",
            )
        if _occupied(stationary, destination):
            return ConstraintResult(
                "occupied", f"Atom {atom.id} ends at an occupied position."
            )
    return _check_ghosts(layout, target_grid, excluded=set())


def check_load(
    device: Device,
    layout: AtomLayout,
    target_grid: AODGrid,
    loads: Mapping[int, tuple[int, int]],
) -> ConstraintResult:
    """Check SLM-to-AOD loading by activation of rows and columns.

    Args:
        device: Device geometry and AOD motion capabilities.
        layout: Validated starting layout.
        target_grid: Existing lines plus new lines; a loaded rigid AOD cannot add
            lines until its carried atoms have been released.
        loads: Atom IDs mapped to destination (row ID, column ID) bindings.

    Returns:
        Success or the first failed activation, alignment, or pickup rule.
    """
    result = _check_grid_change(device, layout, target_grid, "load")
    if not result.ok:
        return result
    atoms = {atom.id: atom for atom in layout.atoms}
    result = _check_atoms(atoms, loads, None)
    if not result.ok:
        return result
    occupied = {
        (atom.holder.row, atom.holder.column)
        for atom in layout.atoms
        if isinstance(atom.holder, AODHolder)
        and atom.holder.aod_id == target_grid.aod_id
    }
    rows, columns = _coordinates(target_grid)
    for atom_id, (row, column) in loads.items():
        if row not in rows or column not in columns:
            return ConstraintResult(
                "alignment", f"Atom {atom_id} references an inactive intersection."
            )
        if not _same_position(
            atoms[atom_id].position, Position(columns[column], rows[row])
        ):
            return ConstraintResult(
                "alignment", f"Atom {atom_id} is not at the loading intersection."
            )
        if (row, column) in occupied:
            return ConstraintResult(
                "occupied", f"Loading atom {atom_id} would occupy an existing trap."
            )
        occupied.add((row, column))
    return _check_ghosts(layout, target_grid, excluded=set(loads))


def check_store(
    device: Device,
    layout: AtomLayout,
    target_grid: AODGrid,
    stores: Mapping[int, str],
) -> ConstraintResult:
    """Check AOD-to-SLM unloading by deactivation of rows and columns.

    Args:
        device: Device geometry and AOD motion capabilities.
        layout: Validated starting layout.
        target_grid: The subset of active lines remaining after unloading.
        stores: Atom IDs mapped to receiving SLM site IDs.

    Returns:
        Success or the first failed deactivation, alignment, or occupancy rule.
    """
    result = _check_grid_change(device, layout, target_grid, "store")
    if not result.ok:
        return result
    atoms = {atom.id: atom for atom in layout.atoms}
    result = _check_atoms(atoms, stores, target_grid.aod_id)
    if not result.ok:
        return result
    occupied_sites = {
        atom.holder.site_id
        for atom in layout.atoms
        if isinstance(atom.holder, SLMHolder)
    }
    rows, columns = _coordinates(target_grid)
    for atom_id, site_id in stores.items():
        if site_id in occupied_sites:
            return ConstraintResult("occupied", f"SLM site {site_id!r} is occupied.")
        try:
            site = device.site(site_id)
        except KeyError:
            return ConstraintResult("alignment", f"Unknown SLM site {site_id!r}.")
        if not _same_position(atoms[atom_id].position, site.position):
            return ConstraintResult(
                "alignment", f"Atom {atom_id} is not at SLM site {site_id!r}."
            )
        occupied_sites.add(site_id)
    for atom in layout.atoms:
        if not _carried_by(atom, target_grid.aod_id):
            continue
        holder = atom.holder
        assert isinstance(holder, AODHolder)
        remains_active = holder.row in rows and holder.column in columns
        if atom.id in stores and remains_active:
            return ConstraintResult(
                "active_trap", f"Atom {atom.id}'s AOD intersection remains active."
            )
        if atom.id not in stores and not remains_active:
            return ConstraintResult(
                "unintended_release", f"Deactivated lines also release atom {atom.id}."
            )
    for grid in layout.aod_grids:
        if grid.aod_id == target_grid.aod_id:
            continue
        rows_at = sorted(line.coordinate for line in grid.rows)
        columns_at = sorted(line.coordinate for line in grid.columns)
        for atom_id in stores:
            position = atoms[atom_id].position
            if _on_axis(rows_at, position.y) and _on_axis(columns_at, position.x):
                return ConstraintResult(
                    "ghost_pickup",
                    f"Stored atom {atom_id} remains under AOD {grid.aod_id!r}.",
                )
    return ConstraintResult()


def _check_grid_change(
    device: Device,
    layout: AtomLayout,
    target: AODGrid,
    operation: Literal["move", "load", "store"],
) -> ConstraintResult:
    try:
        validate_grid(device, target)
    except ValueError as error:
        return ConstraintResult("invalid_grid", str(error))
    source = layout.grid(target.aod_id)
    for before, after in (
        (source.rows, target.rows),
        (source.columns, target.columns),
    ):
        old = {line.id: line.coordinate for line in before}
        new = {line.id: line.coordinate for line in after}
        if operation == "move":
            if old.keys() != new.keys():
                return ConstraintResult(
                    "line_identity", "MOVE must preserve the active line identities."
                )
            if _spatial_order(before) != _spatial_order(after):
                return ConstraintResult("order", "Active AOD lines cannot cross.")
        else:
            keeps_lines = old.keys() <= new.keys()
            if operation == "store":
                keeps_lines = new.keys() <= old.keys()
            if not keeps_lines:
                return ConstraintResult(
                    "line_identity",
                    "LOAD can only add lines; STORE can only remove lines.",
                )
            if any(not _same(old[key], new[key]) for key in old.keys() & new.keys()):
                return ConstraintResult(
                    "line_motion", "A transfer cannot reposition existing AOD lines."
                )
    if device.aod(target.aod_id).motion == "rigid":
        return _check_rigid_change(layout, source, target, operation)
    return ConstraintResult()


def _check_rigid_change(
    layout: AtomLayout,
    source: AODGrid,
    target: AODGrid,
    operation: Literal["move", "load", "store"],
) -> ConstraintResult:
    if operation == "load" and any(
        _carried_by(atom, target.aod_id) for atom in layout.atoms
    ):
        if len(source.rows) != len(target.rows) or len(source.columns) != len(
            target.columns
        ):
            return ConstraintResult(
                "rigid_loading", "A rigid AOD carrying atoms cannot activate new lines."
            )
    if operation == "move":
        for name, before, after in (
            ("row", source.rows, target.rows),
            ("column", source.columns, target.columns),
        ):
            if not before:
                continue
            new = {line.id: line.coordinate for line in after}
            displacement = new[before[0].id] - before[0].coordinate
            if any(
                not _same(new[line.id] - line.coordinate, displacement)
                for line in before
            ):
                return ConstraintResult(
                    "rigid_translation",
                    f"Rigid AOD {name} lines must share one displacement.",
                )
    return ConstraintResult()


def _check_atoms(
    atoms: Mapping[int, AtomState],
    requested: Mapping[int, object],
    aod_id: str | None,
) -> ConstraintResult:
    for atom_id in requested:
        if atom_id not in atoms:
            return ConstraintResult("unknown_atom", f"Unknown atom {atom_id}.")
        atom = atoms[atom_id]
        expected_holder = (
            isinstance(atom.holder, SLMHolder)
            if aod_id is None
            else _carried_by(atom, aod_id)
        )
        if not expected_holder:
            return ConstraintResult(
                "wrong_holder",
                f"Atom {atom_id} has the wrong holder for this operation.",
            )
    return ConstraintResult()


def _check_ghosts(
    layout: AtomLayout, grid: AODGrid, excluded: set[int]
) -> ConstraintResult:
    rows = sorted(line.coordinate for line in grid.rows)
    columns = sorted(line.coordinate for line in grid.columns)
    for atom in layout.atoms:
        if atom.id in excluded or not isinstance(atom.holder, SLMHolder):
            continue
        if _on_axis(rows, atom.position.y) and _on_axis(columns, atom.position.x):
            return ConstraintResult(
                "ghost_pickup", f"An active intersection also traps SLM atom {atom.id}."
            )
    return ConstraintResult()


def _coordinates(grid: AODGrid) -> tuple[dict[int, float], dict[int, float]]:
    return (
        {line.id: line.coordinate for line in grid.rows},
        {line.id: line.coordinate for line in grid.columns},
    )


def _spatial_order(lines: tuple[AODLine, ...]) -> tuple[int, ...]:
    return tuple(line.id for line in sorted(lines, key=lambda line: line.coordinate))


def _carried_by(atom: AtomState, aod_id: str) -> bool:
    return isinstance(atom.holder, AODHolder) and atom.holder.aod_id == aod_id


def _same(left: float, right: float) -> bool:
    return isclose(left, right, rel_tol=0, abs_tol=COORDINATE_TOLERANCE)


def _same_position(left: Position, right: Position) -> bool:
    return _same(left.x, right.x) and _same(left.y, right.y)


def _on_axis(coordinates: list[float], value: float) -> bool:
    index = bisect_left(coordinates, value - COORDINATE_TOLERANCE)
    return index < len(coordinates) and _same(coordinates[index], value)


def _occupied(positions: list[tuple[float, float]], target: Position) -> bool:
    index = bisect_left(positions, (target.x - COORDINATE_TOLERANCE, -inf))
    while index < len(positions):
        x, y = positions[index]
        if x > target.x + COORDINATE_TOLERANCE:
            break
        if _same(y, target.y):
            return True
        index += 1
    return False
