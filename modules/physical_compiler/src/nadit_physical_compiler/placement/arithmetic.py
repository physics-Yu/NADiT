"""Binary32 operations used by the original placement proxy."""

from collections.abc import Iterable
from struct import Struct

_FLOAT = Struct("f")


def f32(value: float) -> float:
    """Round a scalar at an upstream C++ float expression boundary."""
    return float(_FLOAT.unpack(_FLOAT.pack(value))[0])


def sum32(values: Iterable[float], start: float = 0.0) -> float:
    """Accumulate in source iteration order, rounding each float addition."""
    total = f32(start)
    for value in values:
        total = f32(total + value)
    return total
