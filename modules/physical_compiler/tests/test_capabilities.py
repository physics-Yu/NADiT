"""Operation capability records and optional device configuration."""

import json

from nadit_physical_compiler.capabilities import (
    CZCapability,
    LocalControl,
    ReadoutCapability,
)
from nadit_physical_compiler.device import Device, Timing, loads_device


def _config() -> dict[str, object]:
    return {
        "name": "operations",
        "zones": [],
        "aods": [],
        "timing": {"load_us": 10, "store_us": 12, "move_us_per_um": 1},
    }


def test_operation_records_keep_units_and_holder_capabilities() -> None:
    cz = CZCapability("rydberg", 4.6, 0.5)
    readout = ReadoutCapability("camera", 20, latency_us=3, min_spacing_um=5)
    local = LocalControl("raman", 1, 2)

    assert cz.holder_pairs == (("slm", "slm"), ("slm", "aod"), ("aod", "aod"))
    assert (cz.radius_um, cz.duration_us) == (4.6, 0.5)
    assert readout.holders == local.holders == ("slm", "aod")
    assert readout.retain_atoms
    assert (readout.latency_us, readout.min_spacing_um) == (3, 5)
    assert (local.rotation_us, local.reset_us) == (1, 2)


def test_geometry_only_devices_keep_optional_operation_capabilities() -> None:
    device = Device("geometry", (), (), (), Timing(10, 12, 1))
    loaded = loads_device(json.dumps(_config()))

    assert (device.cz, device.readout, device.local) == (None, None, None)
    assert (loaded.cz, loaded.readout, loaded.local) == (None, None, None)


def test_json_loads_operation_records_with_defaults() -> None:
    config = _config()
    config.update(
        cz={"channel": "rydberg", "radius_um": 4.6, "duration_us": 0.5},
        readout={"channel": "camera", "duration_us": 20},
        local={"channel": "raman", "rotation_us": 1, "reset_us": 2},
    )

    device = loads_device(json.dumps(config))

    assert device.cz == CZCapability("rydberg", 4.6, 0.5)
    assert device.readout == ReadoutCapability("camera", 20)
    assert device.local == LocalControl("raman", 1, 2)


def test_json_converts_explicit_holder_lists_to_immutable_tuples() -> None:
    config = _config()
    config.update(
        cz={
            "channel": "rydberg",
            "radius_um": 4.6,
            "duration_us": 0.5,
            "holder_pairs": [["aod", "slm"]],
        },
        readout={
            "channel": "camera",
            "duration_us": 20,
            "latency_us": 3,
            "min_spacing_um": 5,
            "retain_atoms": False,
            "holders": ["aod"],
        },
        local={
            "channel": "raman",
            "rotation_us": 1,
            "reset_us": 2,
            "holders": ["slm"],
        },
    )

    device = loads_device(json.dumps(config))

    assert device.cz == CZCapability("rydberg", 4.6, 0.5, (("aod", "slm"),))
    assert device.readout == ReadoutCapability("camera", 20, 3, 5, False, ("aod",))
    assert device.local == LocalControl("raman", 1, 2, ("slm",))


def test_json_accepts_absent_capabilities_as_null() -> None:
    config = _config()
    config.update(cz=None, readout=None, local=None)

    device = loads_device(json.dumps(config))

    assert (device.cz, device.readout, device.local) == (None, None, None)
