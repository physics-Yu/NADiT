"""Gate channel adapters, cached independently of atom/measurement identities."""

import importlib
from dataclasses import dataclass, field
from math import isclose
from typing import Any

from ...activity import ActivityEvent
from ...provenance import canonical_hash
from ..hsim import HSIM_COMMIT, _backend, _cz_model, _single_result
from .config import probabilities


@dataclass(frozen=True, slots=True)
class GateEffect:
    """Before-ideal Pauli twirl and separate joint computational survival."""

    pauli: tuple[float, ...]
    fidelity: float
    computational_survival: float = 1.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _single_operation(uftqc: Any, config: dict[str, Any], event: ActivityEvent) -> Any:
    backend = config["backend"]
    target = _single_result(
        uftqc, uftqc.IdealRamanTLSModel(), event.parameters
    ).coherent_unitary
    fields = None
    kwargs = config["parameters"]
    if backend == "ideal":
        model = uftqc.IdealRamanTLSModel()
    elif backend == "coherent_overrotation":
        model = uftqc.CoherentOverrotationModel(**kwargs)
    elif backend == "magnetic_detuning":
        physical = importlib.import_module("uftqc.physical_state")
        fields = physical.FieldParameters(
            magnetic=physical.MagneticFieldState(
                vector_t=(0.0, 0.0, config.get("bz_t", 0.0))
            )
        )
        model = uftqc.MagneticDetuningModel(
            duration_s=event.duration_us * 1e-6, **kwargs
        )
    else:
        calibration = uftqc.RamanAtomicCalibration(**config.get("calibration", {}))
        if backend == "raman_calibrated":
            model = uftqc.RamanAdiabaticEliminationModel(
                calibration=calibration, **kwargs
            )
        else:
            model = uftqc.ProfiledRamanAdiabaticEliminationModel(
                calibration=calibration,
                pulse_profile=uftqc.PiecewiseLinearRamanPulse(
                    **config.get("pulse", {})
                ),
                **kwargs,
            )
    operation = uftqc.RamanSingleQubitGate(
        "native", target, model, field_parameters=fields
    ).execute()
    if backend == "magnetic_detuning":
        # Upstream exposes the coherent channel, but not its Pauli twirl.
        np = importlib.import_module("numpy")
        relative = target.conj().T @ operation.coherent_unitary
        paulis = (
            np.array([[0, 1], [1, 0]]),
            np.array([[0, -1j], [1j, 0]]),
            np.diag([1, -1]),
        )
        weights = [float(abs(np.trace(p @ relative)) ** 2 / 4) for p in paulis]
    else:
        weights = operation.error_model.parameters.get(
            "stim_pauli_channel_1", (0.0,) * 3
        )
    return operation, weights


class GateChannels:
    """Evaluate each distinct calibration/target/duration once per report."""

    def __init__(self) -> None:
        """Start an evaluation-local cache; optional dependencies remain lazy."""
        self.cache: dict[str, GateEffect] = {}
        self.uses_hsim = False

    def evaluate(self, config: dict[str, Any], event: ActivityEvent) -> GateEffect:
        """Resolve a configured Pauli channel or a pinned upstream operation."""
        cz = event.kind == "CZPulse"
        if config["backend"] == "pauli":
            weights = tuple(config["probabilities"])
            dimension = 4 if cz else 2
            return GateEffect(weights, 1 - dimension / (dimension + 1) * sum(weights))
        key = canonical_hash(
            {
                "config": config,
                "parameters": event.parameters,
                "cz": cz,
                "duration_us": event.duration_us,
            }
        )
        if key not in self.cache:
            self.cache[key] = self._upstream(config, event)
        return self.cache[key]

    def _upstream(self, config: dict[str, Any], event: ActivityEvent) -> GateEffect:
        uftqc = _backend()
        self.uses_hsim = True
        if event.kind == "CZPulse":
            operation = uftqc.CZGate(_cz_model(uftqc, config)).execute()
            weights = operation.error_model.parameters.get(
                "stim_pauli_channel_2", (0.0,) * 15
            )
        else:
            operation, weights = _single_operation(uftqc, config, event)
        duration = float(operation.duration_s) * 1e6
        if duration and not isclose(
            duration, event.duration_us, rel_tol=1e-6, abs_tol=1e-6
        ):
            raise ValueError(
                f"hsim pulse duration {duration} us differs "
                f"from compiled {event.duration_us} us."
            )
        parameters = operation.error_model.parameters
        survival = 1 - float(parameters.get("stim_erasure_probability", 0.0))
        diagnostic_keys = (
            "average_leakage_probability",
            "average_terminal_leakage_probability",
            "average_unresolved_loss_probability",
            "average_outside_computational_probability",
            "scattering_probability",
            "modeled_randomization_probability",
            "scattering_interpretation",
            "phase_rad",
            "detuning_hz",
        )
        diagnostics = {
            key: parameters[key] for key in diagnostic_keys if key in parameters
        }
        diagnostics.update(
            backend=config["backend"],
            upstream_commit=HSIM_COMMIT,
            representation=operation.error_model.representation,
            pauli_twirl_is_approximation=config["backend"] != "ideal",
            average_gate_fidelity=float(operation.fidelity),
        )
        if "virtual_z_correction_applied" in operation.diagnostics:
            diagnostics["virtual_z_correction_applied"] = operation.diagnostics[
                "virtual_z_correction_applied"
            ]
        return GateEffect(
            tuple(
                probabilities(
                    weights, 15 if event.kind == "CZPulse" else 3, "hsim.pauli"
                )
            ),
            float(operation.fidelity),
            survival,
            diagnostics,
        )
