"""Capability admission for the fixed-home shared-array baseline."""

from ..device import Device
from ..support import UnsupportedJob


def require_capabilities(device: Device, aod_id: str, *, readout: bool = False) -> None:
    """Reject unsupported motion, pulse, or readout capabilities before planning."""
    reasons = []
    if device.aod(aod_id).motion != "flexible":
        reasons.append("requires flexible AOD motion")
    if device.cz is None or ("slm", "slm") not in device.cz.holder_pairs:
        reasons.append("requires SLM-SLM CZ capability")
    if device.cz is None or not device.cz.allow_spectators:
        reasons.append("global shared-array pulses require cz.allow_spectators=true")
    if readout:
        if (
            device.readout is None
            or not device.readout.retain_atoms
            or "aod" not in device.readout.holders
        ):
            reasons.append("requires retained AOD readout for measurement circuits")
    if reasons:
        raise UnsupportedJob(
            [
                {"stage": "strategy", "kind": "nonzoned", "reason": reason}
                for reason in reasons
            ]
        )
