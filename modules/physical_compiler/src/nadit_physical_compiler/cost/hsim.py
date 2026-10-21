"""Pinned uftqc operation-fidelity adapter; no circuit or transport dynamics."""

import importlib
import json
from collections.abc import Mapping
from importlib.metadata import distribution
from math import exp, isclose, log
from typing import Any

from ..activity import AtomActivity
from ..job_fields import keys_at, object_at
from ..provenance import canonical_hash
from .additive import AdditiveModel, resolved_params
from .base import CostReport, require_validated

HSIM_COMMIT = "8e71f80282e70fc03e771d3edee41a30dcb07dfa"


def _backend() -> Any:
    try:
        package = distribution("uftqc")
        direct = json.loads(package.read_text("direct_url.json") or "{}")
        if direct.get("vcs_info", {}).get("commit_id") != HSIM_COMMIT:
            raise ValueError(
                f"hsim-aqc must be installed at pinned commit {HSIM_COMMIT}."
            )
        return importlib.import_module("uftqc")
    except ImportError as error:
        raise ValueError(
            "hsim-aqc is optional; install with uv sync --extra cost-hsim."
        ) from error


class HsimModel:
    """Aggregate upstream average gate fidelities with explicit additive background."""

    name = "hsim-aqc"
    version = HSIM_COMMIT

    def evaluate(
        self,
        activity: AtomActivity,
        result: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> CostReport:
        """Evaluate each distinct local target and CZ calibration once, then count it.

        Units follow upstream: CZ frequencies in MHz, pulse/lifetime times in
        seconds. Nonideal pulse duration must match the compiled duration.
        The returned product is not a propagated circuit-state fidelity.
        """
        require_validated(result)
        keys_at(dict(params), {"single", "cz", "background"}, "hsim.params")
        uftqc = _backend()
        single = {
            "backend": "ideal",
            "parameters": {},
            **object_at(params.get("single", {}), "hsim.single"),
        }
        cz = {
            "backend": "ideal",
            "parameters": {},
            "dissipation": {},
            "pulse": None,
            "integration_substeps_per_interval": 1,
            **object_at(params.get("cz", {}), "hsim.cz"),
        }
        keys_at(single, {"backend", "parameters"}, "hsim.single")
        keys_at(
            cz,
            {
                "backend",
                "parameters",
                "dissipation",
                "pulse",
                "integration_substeps_per_interval",
            },
            "hsim.cz",
        )
        background = resolved_params(
            object_at(params.get("background", {}), "hsim.background")
        )
        if background["cz_infidelity"] or background["single_infidelity"]:
            raise ValueError(
                "hsim background must not double-count CZ or single-gate infidelity."
            )
        if single["backend"] == "ideal":
            if single["parameters"]:
                raise ValueError("Ideal Raman backend has no parameters.")
            single_model = uftqc.IdealRamanTLSModel()
        elif single["backend"] == "coherent_overrotation":
            single_model = uftqc.CoherentOverrotationModel(**single["parameters"])
        else:
            raise ValueError(
                "hsim.single.backend: expected ideal or coherent_overrotation."
            )
        cz_model = _cz_model(uftqc, cz)
        report = AdditiveModel().evaluate(activity, result, background)
        terms = report["metrics"]["contributions"]
        cache: dict[str, tuple[float, float]] = {}
        for event in activity.events:
            if event.kind not in ("CZPulse", "Rotation", "LocalU", "GlobalRY"):
                continue
            key = canonical_hash({"kind": event.kind, "parameters": event.parameters})
            if key not in cache:
                operation = (
                    uftqc.CZGate(cz_model).execute()
                    if event.kind == "CZPulse"
                    else _single_result(uftqc, single_model, event.parameters)
                )
                if operation.fidelity <= 0:
                    raise ValueError(
                        "hsim returned zero fidelity; finite log report unavailable."
                    )
                cache[key] = (
                    float(operation.fidelity),
                    float(operation.duration_s) * 1e6,
                )
            fidelity, calibrated_us = cache[key]
            if calibrated_us and not isclose(
                event.duration_us, calibrated_us, rel_tol=1e-6, abs_tol=1e-6
            ):
                raise ValueError(
                    f"hsim pulse duration {calibrated_us} us differs "
                    f"from compiled {event.duration_us} us."
                )
            kind, count = (
                ("cz", len(event.pairs))
                if event.kind == "CZPulse"
                else ("single", len(event.atoms))
            )
            terms[kind] += count * log(fidelity)
        total = sum(terms.values())
        effective = {"single": single, "cz": cz, "background": background}
        report.update(
            model=self.name,
            version=self.version,
            adapter_version="v1",
            params=effective,
            params_hash=canonical_hash(effective),
        )
        report["metrics"].update(
            log_fidelity=total, fidelity=exp(total), fidelity_kind="gate_product_proxy"
        )
        report["warnings"] = [
            "Product of upstream average gate fidelities; "
            "coherent accumulation is not propagated.",
            "Transfer, idle, readout and heating use the declared additive background.",
            "No microscopic transport or additional spectator/crosstalk simulation.",
        ]
        return report


def _cz_model(uftqc: Any, config: dict[str, Any]) -> Any:
    if config["backend"] == "ideal":
        if config["parameters"] or config["dissipation"] or config["pulse"] is not None:
            raise ValueError("Ideal CZ backend has no physical parameters.")
        return uftqc.IdealRydbergLadderModel()
    kwargs = {
        "parameters": uftqc.FourLevelRydbergParameters(**config["parameters"]),
        "integration_substeps_per_interval": config[
            "integration_substeps_per_interval"
        ],
    }
    if config["pulse"] is not None:
        kwargs["pulse_profile"] = uftqc.PiecewiseLinearRydbergPulse(**config["pulse"])
    if config["backend"] == "four_level":
        if config["dissipation"]:
            raise ValueError("Use open_four_level for dissipation parameters.")
        return uftqc.FourLevelRydbergCZModel(**kwargs)
    if config["backend"] == "open_four_level":
        return uftqc.OpenSystemFourLevelRydbergCZModel(
            **kwargs,
            dissipation=uftqc.FourLevelRydbergDissipation(**config["dissipation"]),
        )
    raise ValueError("hsim.cz.backend: expected ideal, four_level, or open_four_level.")


def _single_result(uftqc: Any, backend: Any, params: dict[str, Any]) -> Any:
    np = importlib.import_module("numpy")
    if "axis" in params:
        if params["axis"] == "Z":
            angle = params["angle"]
            target = np.diag([np.exp(-0.5j * angle), np.exp(0.5j * angle)])
            return uftqc.RamanSingleQubitGate("RZ", target, backend).execute()
        constructor = getattr(uftqc.RamanSingleQubitGate, "r" + params["axis"].lower())
        return constructor(params["angle"], backend).execute()
    theta, phi, lam = params["theta"], params["phi"], params["lam"]
    c, s = np.cos(theta / 2), np.sin(theta / 2)
    target = np.array(
        [
            [c, -np.exp(1j * lam) * s],
            [np.exp(1j * phi) * s, np.exp(1j * (phi + lam)) * c],
        ]
    )
    return uftqc.RamanSingleQubitGate("U", target, backend).execute()
