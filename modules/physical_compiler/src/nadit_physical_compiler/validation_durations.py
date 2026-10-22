"""Independent Device-duration and serialized-metric checks."""

from collections.abc import Mapping
from math import hypot, isclose
from typing import Any

from .atom_ir import (
    AtomAction,
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
)
from .device import Device
from .motion import ConstraintResult
from .state import AtomLayout

TOLERANCE_US = 1e-8


def check_duration(
    device: Device, layout: AtomLayout, action: AtomAction
) -> ConstraintResult:
    """Check fixed durations exactly and Move against its endpoint lower bound."""
    expected: float | None = None
    if isinstance(action, Load):
        expected = device.timing.load_us
    elif isinstance(action, Store):
        expected = device.timing.store_us
    elif isinstance(action, Move):
        atoms = {a.id: a.position for a in layout.atoms}
        if any(atom not in atoms for atom, _ in action.targets):
            return ConstraintResult()  # Spatial validation reports unknown targets.
        distance = max(
            (hypot(p.x - atoms[a].x, p.y - atoms[a].y) for a, p in action.targets),
            default=0,
        )
        expected = device.timing.move_duration(distance)
        if action.duration_us + TOLERANCE_US >= expected:
            return ConstraintResult()
    elif isinstance(action, CZPulse) and device.cz:
        expected = device.cz.duration_us
    elif isinstance(action, Rotation | LocalU) and device.local:
        expected = device.local.rotation_us
    elif isinstance(action, Reset) and device.local:
        expected = device.local.reset_us
    elif isinstance(action, GlobalRY) and device.global_control:
        expected = device.global_control.rotation_us
    elif isinstance(action, Measure) and device.readout:
        expected = device.readout.duration_us
    if expected is None or isclose(
        action.duration_us, expected, rel_tol=0, abs_tol=TOLERANCE_US
    ):
        return ConstraintResult()
    return ConstraintResult(
        "duration_mismatch",
        f"{type(action).__name__}: recorded {action.duration_us} us; "
        f"Device requires {expected} us"
        + (" or more." if isinstance(action, Move) else "."),
    )


def metrics_equal(stored: Any, computed: Any) -> bool:
    """Compare complete metric trees with tolerance only for floating point values."""
    if isinstance(computed, Mapping):
        return (
            isinstance(stored, Mapping)
            and stored.keys() == computed.keys()
            and all(metrics_equal(stored[k], v) for k, v in computed.items())
        )
    if isinstance(computed, list):
        return (
            isinstance(stored, list)
            and len(stored) == len(computed)
            and all(metrics_equal(a, b) for a, b in zip(stored, computed, strict=True))
        )
    if isinstance(computed, float):
        return type(stored) in (int, float) and isclose(
            stored, computed, rel_tol=0, abs_tol=TOLERANCE_US
        )
    return type(stored) is type(computed) and stored == computed
