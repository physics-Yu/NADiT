"""Validate an explicit catalogue once before assigning gate layers."""

from dataclasses import replace

import pytest

from nadit_physical_compiler.capabilities import CZCapability
from nadit_physical_compiler.device import (
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
)
from nadit_physical_compiler.placement import GatePairCatalog


def _device() -> Device:
    return Device(
        "catalogue",
        (Zone("gate", Region(-1, 99, 11, 101), ("cz",)),),
        tuple(
            SLMSite(f"g{index}", Position(x, 100))
            for index, x in enumerate((0, 2, 8, 10))
        ),
        (),
        Timing(1, 1, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
    )


def test_catalogue_retains_explicit_pair_order_and_zone_coverage() -> None:
    device = _device()
    catalog = GatePairCatalog.from_device(device, "gate", (("g2", "g3"), ("g0", "g1")))

    assert catalog.capacity == 2
    assert catalog.pairs == (
        (device.site("g2"), device.site("g3")),
        (device.site("g0"), device.site("g1")),
    )
    assert catalog.pairs[0][0] is device.site("g2")
    assert catalog.coverage is device.zones[0].bounds


@pytest.mark.parametrize(
    "pairs",
    [
        (),
        (("g0", "g0"),),
        (("g0", "g1"), ("g1", "g2")),
        (("g0", "g3"),),
    ],
)
def test_catalogue_rejects_empty_repeated_or_distant_pairs(
    pairs: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(ValueError):
        GatePairCatalog.from_device(_device(), "gate", pairs)


def test_catalogue_rejects_distinct_ids_at_the_same_position() -> None:
    device = _device()
    device = replace(
        device, slm_sites=device.slm_sites + (SLMSite("duplicate", Position(0, 100)),)
    )
    with pytest.raises(ValueError, match="position"):
        GatePairCatalog.from_device(device, "gate", (("g0", "duplicate"),))


def test_catalogue_rejects_unintended_interactions_between_pairs() -> None:
    device = _device()
    device = replace(
        device,
        slm_sites=tuple(
            SLMSite(f"g{index}", Position(x, 100))
            for index, x in enumerate((0, 2, 6, 8))
        ),
    )
    with pytest.raises(ValueError, match="different pairs"):
        GatePairCatalog.from_device(device, "gate", (("g0", "g1"), ("g2", "g3")))


def test_catalogue_requires_all_sites_inside_the_selected_cz_zone() -> None:
    device = replace(_device(), zones=(Zone("gate", Region(0, 100, 8, 100), ("cz",)),))
    with pytest.raises(ValueError, match="zone"):
        GatePairCatalog.from_device(device, "gate", (("g2", "g3"),))


@pytest.mark.parametrize("unsupported", ["zone", "channel", "holders"])
def test_catalogue_requires_cz_permission_and_slm_pair_capability(
    unsupported: str,
) -> None:
    device = _device()
    if unsupported == "zone":
        device = replace(
            device, zones=(Zone("gate", device.zones[0].bounds, ("rotate",)),)
        )
    elif unsupported == "channel":
        device = replace(device, cz=None)
    else:
        assert device.cz is not None
        device = replace(device, cz=replace(device.cz, holder_pairs=(("aod", "aod"),)))
    with pytest.raises(ValueError, match="CZ"):
        GatePairCatalog.from_device(device, "gate", (("g0", "g1"),))
