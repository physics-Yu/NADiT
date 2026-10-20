"""Device operation channels, timing, and allowed trap holders."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CZCapability:
    """A CZ channel and its geometric interaction model.

    Args:
        channel: Control channel used by CZ pulses.
        radius_um: Interaction-distance threshold in micrometers.
        duration_us: Pulse duration in microseconds.
        holder_pairs: Allowed unordered holder-type pairs, using slm and aod.
    """

    channel: str
    radius_um: float
    duration_us: float
    holder_pairs: tuple[tuple[str, str], ...] = (
        ("slm", "slm"),
        ("slm", "aod"),
        ("aod", "aod"),
    )


@dataclass(frozen=True, slots=True)
class ReadoutCapability:
    """A Z-basis readout channel and result availability timing.

    Args:
        channel: Measurement control channel.
        duration_us: Measurement duration in microseconds.
        latency_us: Additional result-delivery time in microseconds.
        min_spacing_um: Minimum separation for concurrent readout.
        retain_atoms: Whether measured atoms remain available afterward.
        holders: Allowed holder types, using slm and aod.
    """

    channel: str
    duration_us: float
    latency_us: float = 0
    min_spacing_um: float = 0
    retain_atoms: bool = True
    holders: tuple[str, ...] = ("slm", "aod")


@dataclass(frozen=True, slots=True)
class LocalControl:
    """An addressed single-atom rotation and reset channel.

    Args:
        channel: Control channel for addressed operations.
        rotation_us: Rotation duration in microseconds.
        reset_us: Reset duration in microseconds.
        holders: Allowed holder types, using slm and aod.
    """

    channel: str
    rotation_us: float
    reset_us: float
    holders: tuple[str, ...] = ("slm", "aod")


@dataclass(frozen=True, slots=True)
class GlobalControl:
    """A global Y-rotation channel illuminating the computation region.

    Args:
        channel: Global optical control channel.
        rotation_us: Duration of one global rotation.
        holders: Allowed holder types.
        region: Optional illuminated bounds (x_min, y_min, x_max, y_max).
            If omitted, use the bounding region of the device's zones.
    """

    channel: str
    rotation_us: float
    holders: tuple[str, ...] = ("slm", "aod")
    region: tuple[float, float, float, float] | None = None
