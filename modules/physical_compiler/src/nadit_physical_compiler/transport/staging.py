"""Atom-level staged transport following QMAP's rearrangement generator."""

# Adapted from MQT QMAP CodeGenerator.cpp (MIT), revision
# cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d.
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose, trunc
from typing import Literal

from ..device import Position
from ..state import COORDINATE_TOLERANCE, AtomLayout, AtomState, SLMHolder
from .rules import MoveRequest
from .staging_geometry import SiteKind, SiteStagingGeometry, StagingGeometry


@dataclass(frozen=True, slots=True)
class StageEvent:
    """A reference-style atom event, before AOD-frame and timing binding.

    Args:
        kind: Load, position change, or store.
        positions: Affected atom IDs and their coordinates after the event.
            IDs are sorted. Move events contain only changed carried atoms.
    """

    kind: Literal["load", "move", "store"]
    positions: tuple[tuple[int, Position], ...]


def generate_stages(
    moves: tuple[MoveRequest, ...],
    layout: AtomLayout,
    destinations: Mapping[int, AtomState],
    geometry: StagingGeometry,
) -> tuple[StageEvent, ...]:
    """Expand one admitted batch into staged loading, movement, and unloading.

    Args:
        moves: Equality-preserving requests admitted by the constructed template.
        layout: Starting SLM holders for the batch atoms.
        destinations: Target SLM holders, indexed by atom ID.
        geometry: Recognized or explicitly supplied SLM subarray geometry.
            As in the source, the first mover supplies the batch lattice bounds.
            Actual constructed grids determine resource and occupancy admission.

    Returns:
        Atom events with stable active row and column identities. Geometry
        admission and conversion to complete AOD grids belong to the adapter.

    Raises:
        KeyError: If a requested atom or its site geometry is absent.
    """
    if not moves:
        return ()
    return _Generator(moves, layout, destinations, geometry).generate()


class _Generator:
    def __init__(
        self,
        moves: tuple[MoveRequest, ...],
        layout: AtomLayout,
        destinations: Mapping[int, AtomState],
        geometry: StagingGeometry,
    ) -> None:
        self.coordinate_quantum = geometry.coordinate_quantum
        self.moves = {move.atom_id: move for move in moves}
        atoms = {atom.id: atom for atom in layout.atoms}
        self.source_kinds: dict[int, SiteKind] = {}
        self.target_kinds: dict[int, SiteKind] = {}
        source_shapes: dict[int, SiteStagingGeometry] = {}
        target_shapes: dict[int, SiteStagingGeometry] = {}
        for move in moves:
            before, after = (
                atoms[move.atom_id].holder,
                destinations[move.atom_id].holder,
            )
            assert isinstance(before, SLMHolder) and isinstance(after, SLMHolder)
            source_shapes[move.atom_id] = geometry.sites[before.site_id]
            target_shapes[move.atom_id] = geometry.sites[after.site_id]
            self.source_kinds[move.atom_id] = source_shapes[move.atom_id].kind
            self.target_kinds[move.atom_id] = target_shapes[move.atom_id].kind
        self.source = source_shapes[moves[0].atom_id]
        self.target = target_shapes[moves[0].atom_id]
        self.horizontal = dict(sorted((move.source.x, move.target.x) for move in moves))
        self.vertical = dict(sorted((move.source.y, move.target.y) for move in moves))
        self.row_ordered = list(self.vertical.values()) == sorted(
            self.vertical.values()
        )
        self.column_ordered = list(self.horizontal.values()) == sorted(
            self.horizontal.values()
        )
        self.sx = {x: index for index, x in enumerate(self.horizontal)}
        self.sy = {
            source: index
            for index, source in enumerate(
                sorted(self.vertical, key=self.vertical.__getitem__)
            )
        }
        self.tx = {
            target: self.sx[source] for source, target in self.horizontal.items()
        }
        self.ty = {target: self.sy[source] for source, target in self.vertical.items()}
        first_y = next(iter(self.vertical))
        self.up = first_y < self.vertical[first_y]
        self.rows: dict[int, float] = {}
        self.columns: dict[int, float] = {}
        self.carried: dict[int, Position] = {}
        self.events: list[StageEvent] = []

    def generate(self) -> tuple[StageEvent, ...]:
        if self.row_ordered and len(self.horizontal) < len(self.vertical):
            self._load_columns()
            self._store_columns()
        elif self.column_ordered and len(self.vertical) < len(self.horizontal):
            self._load_rows()
            self._store_rows()
        else:
            self._load_rows()
            self._store_columns()
        return tuple(self.events)

    def _divide(self, value: float, divisor: int) -> float:
        """Apply either continuous offsets or source integer-unit division."""
        if self.coordinate_quantum is None:
            return value / divisor
        return (
            trunc(value / (divisor * self.coordinate_quantum)) * self.coordinate_quantum
        )

    def _groups(self, source: bool, row: bool) -> dict[float, set[int]]:
        groups: dict[float, set[int]] = {}
        for atom, move in self.moves.items():
            point = move.source if source else move.target
            coordinate = point.y if row else point.x
            groups.setdefault(coordinate, set()).add(atom)
        return dict(sorted(groups.items()))

    def _mask(self, atoms: set[int], source: bool, row: bool) -> set[float]:
        points = [
            self.moves[atom].source if source else self.moves[atom].target
            for atom in atoms
        ]
        return {point.x if row else point.y for point in points}

    def _move(self, source: bool) -> None:
        xs, ys = (self.sx, self.sy) if source else (self.tx, self.ty)
        changed: list[tuple[int, Position]] = []
        for atom in sorted(self.carried):
            endpoint = self.moves[atom].source if source else self.moves[atom].target
            position = Position(self.columns[xs[endpoint.x]], self.rows[ys[endpoint.y]])
            old = self.carried[atom]
            if not (_same(position.x, old.x) and _same(position.y, old.y)):
                changed.append((atom, position))
                self.carried[atom] = position
        if changed:
            self.events.append(StageEvent("move", tuple(changed)))

    def _load(self, atoms: set[int]) -> None:
        positions = tuple((atom, self.moves[atom].source) for atom in sorted(atoms))
        self.events.append(StageEvent("load", positions))
        self.carried.update(positions)

    def _store(self, atoms: set[int]) -> None:
        positions = tuple((atom, self.moves[atom].target) for atom in sorted(atoms))
        self.events.append(StageEvent("store", positions))
        for atom in atoms:
            del self.carried[atom]

    def _row_x(self, atom: int, source: bool) -> float:
        move = self.moves[atom]
        shape = self.source if source else self.target
        kind = self.source_kinds[atom] if source else self.target_kinds[atom]
        x = move.source.x if source else move.target.x
        if kind == "storage":
            return x + self._divide(shape.pitch_x, 2)
        return x + (-1 if kind == "left" else 1) * self._divide(shape.pitch_x, 4)

    def _column_x(self, atom: int, source: bool) -> float:
        move = self.moves[atom]
        shape = self.source if source else self.target
        kind = self.source_kinds[atom] if source else self.target_kinds[atom]
        x = move.source.x if source else move.target.x
        if kind == "storage":
            return x + self._divide(shape.pitch_x, 2)
        offset = self._divide(shape.pitch_x - shape.pair_separation, 3)
        return x + (-offset if kind == "left" else offset)

    def _load_rows(self) -> None:
        groups = self._groups(True, True)
        dy = self.source.pitch_y
        sign = 1 if self.up else -1
        for source_y, group in groups.items():
            row = self.sy[source_y]
            if row in self.rows:
                continue
            loading = set(group)
            self.rows[row] = y = source_y + sign * self._divide(dy, 2)
            next_y = y - dy
            for previous in _below(self.rows, row):
                if self.rows[previous] <= next_y:
                    break
                self.rows[previous] = next_y
                next_y -= dy if next_y > self.source.min_y else self._divide(dy, 2)
            next_y = y + dy
            previous = row
            for upper in [*_above(self.rows, row), None]:
                threshold = next_y - sign * self._divide(dy, 2)
                for another_y, another_group in groups.items():
                    another = self.sy[another_y]
                    if (
                        another_y >= threshold
                        and previous < another
                        and (upper is None or another < upper)
                        and self._mask(group, True, True)
                        == self._mask(another_group, True, True)
                    ):
                        self.rows[another] = another_y + sign * self._divide(dy, 2)
                        loading.update(another_group)
                        next_y = another_y + dy + sign * self._divide(dy, 2)
                        previous = another
                if upper is None:
                    break
                if self.rows[upper] < next_y:
                    self.rows[upper] = next_y
                    next_y += dy if next_y < self.source.max_y else self._divide(dy, 2)
                previous = upper
            self._move(True)
            for atom in group:
                self.columns[self.sx[self.moves[atom].source.x]] = self.moves[
                    atom
                ].source.x
            self._move(True)
            for atom in loading:
                self.columns[self.sx[self.moves[atom].source.x]] = self._row_x(
                    atom, True
                )
            self._load(loading)
            self._move(True)

    def _load_columns(self) -> None:
        groups = self._groups(True, False)
        sign = 1 if self.up else -1
        for source_x, group in groups.items():
            column = self.sx[source_x]
            if column in self.columns:
                continue
            loading = set(group)
            kind = self.source_kinds[min(group)]
            if kind == "storage":
                self._load_storage_column(source_x, column, group, loading, groups)
            else:
                self._load_gate_column(source_x, column, group, loading, groups, kind)
            self._move(True)
            for atom in group:
                self.rows[self.sy[self.moves[atom].source.y]] = self.moves[
                    atom
                ].source.y
            self._move(True)
            for atom in loading:
                y = self.moves[atom].source.y
                self.rows[self.sy[y]] = y + sign * self._divide(self.source.pitch_y, 2)
            self._load(loading)
            self._move(True)

    def _load_storage_column(
        self,
        x: float,
        column: int,
        group: set[int],
        loading: set[int],
        groups: dict[float, set[int]],
    ) -> None:
        dx = self.source.pitch_x
        self.columns[column] = x + self._divide(dx, 2)
        next_x = x - self._divide(dx, 2)
        for previous in _below(self.columns, column):
            if self.columns[previous] <= next_x:
                break
            self.columns[previous] = next_x
            next_x -= dx if next_x > self.source.min_x else self._divide(dx, 2)
        next_x = x + dx + self._divide(dx, 2)
        previous = column
        for upper in [*_above(self.columns, column), None]:
            threshold = next_x - self._divide(dx, 2)
            for another_x, another_group in groups.items():
                another = self.sx[another_x]
                if (
                    another_x >= threshold
                    and previous < another
                    and (upper is None or another < upper)
                    and self._mask(group, True, False)
                    == self._mask(another_group, True, False)
                ):
                    self.columns[another] = another_x + self._divide(dx, 2)
                    loading.update(another_group)
                    next_x = another_x + dx + self._divide(dx, 2)
                    previous = another
            if upper is None:
                break
            if self.columns[upper] < next_x:
                self.columns[upper] = next_x
                next_x += dx if next_x < self.source.max_x else self._divide(dx, 2)
            previous = upper

    def _load_gate_column(
        self,
        x: float,
        column: int,
        group: set[int],
        loading: set[int],
        groups: dict[float, set[int]],
        kind: SiteKind,
    ) -> None:
        dx, separation = self.source.pitch_x, self.source.pair_separation
        offset = self._divide(dx - separation, 3)
        left = kind == "left"
        self.columns[column] = x + (-offset if left else offset)
        next_x = x - (dx - separation - offset if left else separation + offset)
        for previous in _below(self.columns, column):
            if self.columns[previous] <= next_x:
                break
            self.columns[previous] = next_x
            next_x -= dx - offset if next_x > self.source.min_x and left else offset
            left = not left
        left = kind == "left"
        next_x = x + (separation + offset if left else dx - separation - offset)
        previous = column
        for upper in [*_above(self.columns, column), None]:
            threshold = next_x - (
                separation + offset if left else dx - separation - offset
            )
            for another_x, another_group in groups.items():
                another = self.sx[another_x]
                if (
                    another_x >= threshold
                    and previous < another
                    and (upper is None or another < upper)
                    and self._mask(group, True, False)
                    == self._mask(another_group, True, False)
                ):
                    left = self.source_kinds[min(another_group)] == "left"
                    self.columns[another] = another_x + (-offset if left else offset)
                    loading.update(another_group)
                    next_x = another_x + (
                        separation + offset if left else dx - separation - offset
                    )
                    previous = another
            if upper is None:
                break
            if self.columns[upper] < next_x:
                self.columns[upper] = next_x
                next_x += offset if next_x >= self.source.max_x or left else dx - offset
                left = not left
            previous = upper

    def _store_rows(self) -> None:
        groups = self._groups(False, True)
        dy = self.target.pitch_y
        sign = -1 if self.up else 1
        targets = {self.ty[y]: y for y in groups}
        self.rows.update(
            (row, y + sign * self._divide(dy, 2)) for row, y in targets.items()
        )
        for atom, move in self.moves.items():
            self.columns[self.tx[move.target.x]] = self._row_x(atom, False)
        first_row = self.ty[next(iter(groups))]
        next_y = self.rows[first_row]
        for previous in _below(self.rows, first_row):
            next_y -= dy if next_y > self.target.min_y else self._divide(dy, 2)
            self.rows[previous] = min(self.rows[previous], next_y)
            next_y = self.rows[previous]
        next_y = self.rows[first_row]
        for upper in _above(self.rows, first_row):
            next_y += dy if next_y < self.target.max_y else self._divide(dy, 2)
            self.rows[upper] = max(self.rows[upper], next_y)
            next_y = self.rows[upper]
        for y, group in groups.items():
            row = self.ty[y]
            if row not in self.rows:
                continue
            storing = set(group)
            aligned = [(row, y)]
            self.rows[row] = offset_y = y + sign * self._divide(dy, 2)
            next_y = offset_y - dy
            for previous in _below(self.rows, row):
                if self.rows[previous] <= next_y:
                    break
                self.rows[previous] = next_y
                next_y -= dy if next_y > self.target.min_y else self._divide(dy, 2)
            next_y = offset_y + dy
            free = 0
            for upper in _above(self.rows, row):
                other_y, other = targets[upper], groups[targets[upper]]
                if other_y >= next_y - sign * self._divide(
                    dy, 2
                ) + free * dy and self._mask(group, False, True) == self._mask(
                    other, False, True
                ):
                    aligned.append((upper, other_y))
                    self.rows[upper] = other_y + sign * self._divide(dy, 2)
                    storing.update(other)
                    next_y = other_y - sign * self._divide(dy, 2)
                    for previous in _below(self.rows, upper):
                        if self.rows[previous] <= next_y:
                            break
                        self.rows[previous] = next_y
                        next_y -= (
                            dy if next_y > self.target.min_y else self._divide(dy, 2)
                        )
                    next_y = other_y + dy + sign * self._divide(dy, 2)
                    free = 0
                elif self.rows[upper] < next_y:
                    self.rows[upper] = next_y
                    next_y += dy if next_y < self.target.max_y else self._divide(dy, 2)
                else:
                    free += 1
            self._move(False)
            for atom in group:
                x = self.moves[atom].target.x
                self.columns[self.tx[x]] = x
            for row_id, target_y in aligned:
                self.rows[row_id] = target_y
            self._move(False)
            for atom in storing:
                self.columns[self.tx[self.moves[atom].target.x]] = self._row_x(
                    atom, False
                )
            self._store(storing)
            for row_id, _ in aligned:
                del self.rows[row_id]
            self._move(False)

    def _store_columns(self) -> None:
        groups = self._groups(False, False)
        sign = -1 if self.up else 1
        targets = {self.tx[x]: x for x in groups}
        self.rows.update(
            (row, y + sign * self._divide(self.target.pitch_y, 2))
            for y, row in self.ty.items()
        )
        for x, group in groups.items():
            self.columns[self.tx[x]] = self._column_x(min(group), False)
        self._spread_target_columns(groups)
        for x, group in groups.items():
            column = self.tx[x]
            if column not in self.columns:
                continue
            storing = set(group)
            aligned = [(column, x)]
            kind = self.target_kinds[min(group)]
            if kind == "storage":
                self._store_storage_column(
                    x, column, group, storing, aligned, groups, targets
                )
            else:
                self._store_gate_column(
                    x, column, group, storing, aligned, groups, targets, kind
                )
            self._move(False)
            for atom in group:
                y = self.moves[atom].target.y
                self.rows[self.ty[y]] = y
            for column_id, target_x in aligned:
                self.columns[column_id] = target_x
            self._move(False)
            for atom in storing:
                y = self.moves[atom].target.y
                self.rows[self.ty[y]] = y + sign * self._divide(self.target.pitch_y, 2)
            self._store(storing)
            for column_id, _ in aligned:
                del self.columns[column_id]
            self._move(False)

    def _spread_target_columns(self, groups: dict[float, set[int]]) -> None:
        first_x = next(iter(groups))
        column = self.tx[first_x]
        first_kind = self.target_kinds[min(groups[first_x])]
        dx = self.target.pitch_x
        offset = self._divide(dx - self.target.pair_separation, 3)
        kind = first_kind
        next_x = self.columns[column]
        for previous in _below(self.columns, column):
            if kind == "storage":
                next_x -= dx if next_x > self.target.min_x else self._divide(dx, 2)
            else:
                next_x -= (
                    dx - offset
                    if next_x > self.target.min_x and kind == "right"
                    else offset
                )
                kind = _flip(kind)
            if self.columns[previous] > next_x:
                self.columns[previous] = next_x
            else:
                if kind != "storage" and not _multiple(
                    next_x - self.columns[previous], dx
                ):
                    kind = _flip(kind)
                next_x = self.columns[previous]
        kind = first_kind
        next_x = self.columns[column]
        for upper in _above(self.columns, column):
            if kind == "storage":
                next_x += dx if next_x > self.target.min_x else self._divide(dx, 2)
            else:
                # Ascending lanes use the upper boundary. Using min_x here
                # corrupts left/right parity when starting at the left margin.
                next_x += (
                    dx - offset
                    if next_x < self.target.max_x and kind == "left"
                    else offset
                )
                kind = _flip(kind)
            if self.columns[upper] < next_x:
                self.columns[upper] = next_x
            else:
                if kind != "storage" and not _multiple(
                    self.columns[upper] - next_x, dx
                ):
                    kind = _flip(kind)
                next_x = self.columns[upper]

    def _store_storage_column(
        self,
        x: float,
        column: int,
        group: set[int],
        storing: set[int],
        aligned: list[tuple[int, float]],
        groups: dict[float, set[int]],
        targets: dict[int, float],
    ) -> None:
        dx = self.target.pitch_x
        self.columns[column] = x + self._divide(dx, 2)
        next_x = x - self._divide(dx, 2)
        for previous in _below(self.columns, column):
            if self.columns[previous] <= next_x:
                break
            self.columns[previous] = next_x
            next_x -= dx if next_x > self.target.min_x else self._divide(dx, 2)
        next_x = x + dx + self._divide(dx, 2)
        free = 0
        for upper in _above(self.columns, column):
            other_x, other = targets[upper], groups[targets[upper]]
            if other_x >= next_x + free * dx - self._divide(dx, 2) and self._mask(
                group, False, False
            ) == self._mask(other, False, False):
                aligned.append((upper, other_x))
                self.columns[upper] = other_x + self._divide(dx, 2)
                storing.update(other)
                next_x = other_x - self._divide(dx, 2)
                for previous in _below(self.columns, upper):
                    if self.columns[previous] <= next_x:
                        break
                    self.columns[previous] = next_x
                    next_x -= dx if next_x > self.target.min_x else self._divide(dx, 2)
                next_x = other_x + dx + self._divide(dx, 2)
                free = 0
            elif self.columns[upper] < next_x:
                self.columns[upper] = next_x
                next_x += dx if next_x < self.target.max_x else self._divide(dx, 2)
                free = 0
            else:
                free += 1

    def _store_gate_column(
        self,
        x: float,
        column: int,
        group: set[int],
        storing: set[int],
        aligned: list[tuple[int, float]],
        groups: dict[float, set[int]],
        targets: dict[int, float],
        kind: SiteKind,
    ) -> None:
        dx, separation = self.target.pitch_x, self.target.pair_separation
        offset = self._divide(dx - separation, 3)
        left = kind == "left"
        self.columns[column] = x + (-offset if left else offset)
        next_x = x - (dx - separation - offset if left else separation + offset)
        for previous in _below(self.columns, column):
            if self.columns[previous] <= next_x:
                break
            self.columns[previous] = next_x
            next_x -= dx - offset if next_x > self.target.min_x and left else offset
            left = not left
        left = kind == "left"
        next_x = x + (separation + offset if left else dx - separation - offset)
        free = 0
        for upper in _above(self.columns, column):
            other_x, other = targets[upper], groups[targets[upper]]
            threshold = (
                next_x
                - (offset if left else -offset)
                + (free // 2) * dx
                + ((offset if left else dx - offset) if free % 2 else 0)
            )
            if other_x >= threshold and self._mask(group, False, False) == self._mask(
                other, False, False
            ):
                left = self.target_kinds[min(other)] == "left"
                aligned.append((upper, other_x))
                self.columns[upper] = other_x + (-offset if left else offset)
                storing.update(other)
                next_x = other_x - (
                    dx - separation - offset if left else separation + offset
                )
                for previous in _below(self.columns, upper):
                    if self.columns[previous] <= next_x:
                        break
                    self.columns[previous] = next_x
                    next_x -= (
                        dx - offset if next_x > self.target.min_x and left else offset
                    )
                    left = not left
                left = self.target_kinds[min(other)] == "left"
                next_x = other_x + (
                    separation + offset if left else dx - separation - offset
                )
                free = 0
            elif self.columns[upper] < next_x:
                self.columns[upper] = next_x
                next_x += offset if next_x >= self.target.max_x or left else dx - offset
                left = not left
                free = 0
            else:
                free += 1


def _above(coordinates: Mapping[int, float], index: int) -> list[int]:
    return sorted(key for key in coordinates if key > index)


def _below(coordinates: Mapping[int, float], index: int) -> list[int]:
    return sorted((key for key in coordinates if key < index), reverse=True)


def _flip(kind: SiteKind) -> SiteKind:
    return "right" if kind == "left" else "left"


def _same(first: float, second: float) -> bool:
    return isclose(first, second, rel_tol=0, abs_tol=COORDINATE_TOLERANCE)


def _multiple(value: float, period: float) -> bool:
    return _same(value % period, 0) or _same(value % period, period)
