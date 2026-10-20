"""Relaxed pair compatibility and the reference cubed-offset cost."""

# Adapted from MQT QMAP IndependentSetRouter.cpp/.hpp (MIT), revision
# cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d.
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from dataclasses import dataclass
from math import cbrt
from typing import Literal

from ..device import AOD, Timing
from ..state import COORDINATE_TOLERANCE
from .rules import MoveRequest, StrictRules


@dataclass(frozen=True, slots=True)
class MovementCompatibility:
    """Pair classification and optional cubed-offset proxy in distance units.

    Strict and incompatible pairs have no merge cost. Relaxed pairs carry the
    source router's extra-offset proxy; it is not a device duration.
    """

    status: Literal["strict", "relaxed", "incompatible"]
    merge_cost: float | None = None


@dataclass(frozen=True, slots=True)
class RelaxedRules:
    """Preserve row/column equality while allowing staged order changes.

    Args:
        aod: Flexible device workspace, spacing, and line capacity.
        timing: Device timing retained alongside compatibility rules.
    """

    aod: AOD
    timing: Timing

    def compatibility(
        self, first: MoveRequest, second: MoveRequest
    ) -> MovementCompatibility:
        """Classify two requests and compute their reference merge cost.

        Equality must be preserved on each axis. Distinct endpoint lines must
        satisfy the AOD spacing; their relative order may change.
        """
        reversals: list[float] = []
        for source_delta, target_delta in (
            (first.source.x - second.source.x, first.target.x - second.target.x),
            (first.source.y - second.source.y, first.target.y - second.target.y),
        ):
            source_shared = abs(source_delta) <= COORDINATE_TOLERANCE
            target_shared = abs(target_delta) <= COORDINATE_TOLERANCE
            if source_shared != target_shared:
                return MovementCompatibility("incompatible")
            if source_shared:
                continue
            if min(abs(source_delta), abs(target_delta)) < (
                self.aod.min_spacing - COORDINATE_TOLERANCE
            ):
                return MovementCompatibility("incompatible")
            if (source_delta < 0) != (target_delta < 0):
                reversals.append(abs(source_delta) + abs(target_delta))
        if not reversals:
            return MovementCompatibility("strict")
        cost = (
            reversals[0]
            if len(reversals) == 1
            else _sum_cube_roots_cubed(reversals[0], reversals[1])
        )
        return MovementCompatibility("relaxed", cost)

    def compatible(self, first: MoveRequest, second: MoveRequest) -> bool:
        """Return whether a pair is strict or relaxed compatible."""
        return self.compatibility(first, second).status != "incompatible"

    def can_fit(self, moves: tuple[MoveRequest, ...]) -> bool:
        """Check endpoint workspace, line counts, and spacing.

        Requests are assumed pairwise relaxed compatible. This does not require
        a single active MOVE to preserve order; staged construction owns that.
        """
        return StrictRules(self.aod, self.timing).can_fit(moves)


def _sum_cube_roots_cubed(first: float, second: float) -> float:
    a, b = cbrt(first), cbrt(second)
    return first + second + 3 * a * b * (a + b)


def _sub_cube_roots_cubed(first: float, second: float) -> float:
    a, b = cbrt(first), cbrt(second)
    return first - second + 3 * a * b * (b - a)
