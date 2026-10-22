"""JSON configuration for independent physical error contributions."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ...job_fields import boolean_at, keys_at, number_at, object_at

TRANSPORT_DEFAULTS: dict[str, Any] = {
    "backend": "rates/v1",
    "load_loss": 0.0,
    "store_loss": 0.0,
    "move_loss": 0.0,
    "loss_per_um": 0.0,
    "loss_per_us": 0.0,
    "aod_loss_per_us": 0.0,
    "slm_loss_per_us": 0.0,
    "move_pauli": [0.0, 0.0, 0.0],
}
EXPOSURE_DEFAULTS: dict[str, Any] = {
    "slm_t2_us": None,
    "aod_t2_us": None,
    "include_transport": True,
}
DEFAULTS: dict[str, Any] = {
    "single": {"backend": "pauli", "probabilities": [0.0] * 3},
    "single_overrides": {},
    "cz": {"backend": "pauli", "probabilities": [0.0] * 15},
    "transport": TRANSPORT_DEFAULTS,
    "exposure": EXPOSURE_DEFAULTS,
    "readout_flip": 0.0,
    "reset_flip": 0.0,
    "cz_atom_loss": 0.0,
    "spectator_pauli": [0.0] * 3,
}
SINGLE_BACKENDS = [
    "pauli",
    "ideal",
    "coherent_overrotation",
    "magnetic_detuning",
    "raman_calibrated",
    "raman_profiled",
]
CZ_BACKENDS = ["pauli", "ideal", "four_level", "open_four_level"]


def probability(value: Any, path: str) -> float:
    """Read a finite probability including zero and certain error."""
    p = number_at(value, path)
    if not 0 <= p <= 1:
        raise ValueError(f"{path}: expected a probability in [0, 1].")
    return p


def probabilities(value: Any, size: int, path: str) -> list[float]:
    """Validate mutually exclusive nonidentity Pauli weights."""
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{path}: expected {size} Pauli probabilities.")
    values = [probability(p, path) for p in value]
    if sum(values) > 1 + 1e-12:
        raise ValueError(f"{path}: Pauli probabilities sum exceeds one.")
    return values


def gate_config(raw: Any, *, cz: bool = False) -> dict[str, Any]:
    """Resolve a configured channel or an explicit upstream backend request."""
    value = object_at(raw, "noise.gate")
    backend = value.get("backend", "pauli")
    if backend not in (CZ_BACKENDS if cz else SINGLE_BACKENDS):
        raise ValueError(f"noise.gate: unknown backend {backend!r}.")
    if backend == "pauli":
        keys_at(value, {"backend", "probabilities"}, "noise.gate")
        size = 15 if cz else 3
        return {
            "backend": backend,
            "probabilities": probabilities(
                value.get("probabilities", [0.0] * size), size, "noise.gate"
            ),
        }
    allowed = {"backend", "parameters"}
    if cz:
        allowed |= {"dissipation", "pulse", "integration_substeps_per_interval"}
    if backend.startswith("raman_"):
        allowed.add("calibration")
    if backend == "raman_profiled":
        allowed.add("pulse")
    if backend == "magnetic_detuning":
        allowed |= {"bz_t"}
    keys_at(value, allowed, "noise.gate")
    resolved = {"backend": backend, "parameters": {}, **deepcopy(value)}
    if cz:
        resolved = {
            "dissipation": {},
            "pulse": None,
            "integration_substeps_per_interval": 1,
            **resolved,
        }
    if backend == "ideal" and resolved["parameters"]:
        raise ValueError("noise.ideal: no parameters supported.")
    return resolved


def resolve(params: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve defaults and reject unknown parameters at the JSON boundary."""
    keys_at(dict(params), set(DEFAULTS), "noise.params")
    p = {**deepcopy(DEFAULTS), **deepcopy(dict(params))}
    p["single"] = gate_config(p["single"])
    p["cz"] = gate_config(p["cz"], cz=True)
    overrides = object_at(p["single_overrides"], "noise.single_overrides")
    keys_at(overrides, {"X", "Y", "Z", "U"}, "noise.single_overrides")
    p["single_overrides"] = {k: gate_config(v) for k, v in overrides.items()}
    for section, defaults in (
        ("transport", TRANSPORT_DEFAULTS),
        ("exposure", EXPOSURE_DEFAULTS),
    ):
        raw = object_at(p[section], f"noise.{section}")
        keys_at(raw, set(defaults), f"noise.{section}")
        p[section] = {**deepcopy(defaults), **raw}
    t = p["transport"]
    if t["backend"] != "rates/v1":
        raise ValueError(
            "noise.transport: only rates/v1 is implemented; "
            "detailed dynamics is an extension interface."
        )
    for key in ("load_loss", "store_loss", "move_loss"):
        t[key] = probability(t[key], key)
    for key in ("loss_per_um", "loss_per_us", "aod_loss_per_us", "slm_loss_per_us"):
        t[key] = number_at(t[key], key)
        if t[key] < 0:
            raise ValueError(f"{key}: expected a nonnegative hazard.")
    t["move_pauli"] = probabilities(t["move_pauli"], 3, "move_pauli")
    for key in ("slm_t2_us", "aod_t2_us"):
        value = p["exposure"][key]
        if value is not None and number_at(value, key) <= 0:
            raise ValueError(f"{key}: expected positive T2 or null.")
    boolean_at(p["exposure"]["include_transport"], "include_transport")
    for key in ("readout_flip", "reset_flip", "cz_atom_loss"):
        p[key] = probability(p[key], key)
    p["spectator_pauli"] = probabilities(p["spectator_pauli"], 3, "spectator_pauli")
    return p
