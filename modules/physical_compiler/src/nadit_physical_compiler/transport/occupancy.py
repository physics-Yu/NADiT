"""Incremental position lookup using the atom-state coordinate tolerance."""

from collections.abc import Mapping
from math import floor

from ..device import Position
from ..state import COORDINATE_TOLERANCE


class Occupancy:
    """Index current atom positions in tolerance-sized spatial buckets.

    Args:
        positions: Atom IDs and their current positions.

    Raises:
        ValueError: If two atoms occupy the same position within tolerance.
    """

    def __init__(self, positions: Mapping[int, Position]) -> None:
        self._positions: dict[int, Position] = {}
        self._buckets: dict[tuple[int, int], set[int]] = {}
        for atom_id, position in positions.items():
            self.add(atom_id, position)

    def at(self, position: Position) -> set[int]:
        """Return atoms within coordinate tolerance in the nine nearby buckets."""
        x, y = _bucket(position)
        return {
            atom_id
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for atom_id in self._buckets.get((x + dx, y + dy), ())
            if abs(self._positions[atom_id].x - position.x) <= COORDINATE_TOLERANCE
            and abs(self._positions[atom_id].y - position.y) <= COORDINATE_TOLERANCE
        }

    def remove(self, atom_id: int) -> None:
        """Remove an atom before reinsertion; missing IDs raise KeyError."""
        position = self._positions.pop(atom_id)
        bucket = _bucket(position)
        entries = self._buckets[bucket]
        entries.remove(atom_id)
        if not entries:
            del self._buckets[bucket]

    def add(self, atom_id: int, position: Position) -> None:
        """Insert an unindexed atom; raise ValueError for an occupied position."""
        if self.at(position):
            raise ValueError(f"Atom {atom_id} shares an occupied position.")
        self._positions[atom_id] = position
        self._buckets.setdefault(_bucket(position), set()).add(atom_id)


def _bucket(position: Position) -> tuple[int, int]:
    return (
        floor(position.x / COORDINATE_TOLERANCE),
        floor(position.y / COORDINATE_TOLERANCE),
    )
