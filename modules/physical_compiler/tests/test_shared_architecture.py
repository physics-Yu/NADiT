"""Shared-array geometry matches the existing hand-written device exactly."""

import json
from pathlib import Path

from nadit_physical_compiler.architectures import generate
from nadit_physical_compiler.device import load_device, loads_device
from nadit_physical_compiler.serialization import encode_device

MODULE = Path(__file__).resolve().parents[1]


def test_exact_shared_device() -> None:
    specification = json.loads(
        (MODULE / "examples/architectures/nonzoned.json").read_text()
    )
    document = generate(specification["family"], specification["params"])
    assert document == encode_device(
        load_device(MODULE / "examples/devices/nonzoned.json")
    )
    specification["params"]["shared_arrays"][0]["pairs_per_row"] = 3
    changed = loads_device(json.dumps(generate("shared/v1", specification["params"])))
    assert len(changed.slm_sites) == 12
