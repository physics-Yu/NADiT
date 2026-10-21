"""Versioned JSON documents for executable Atom IR and compilation results."""

import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .atom_ir import (
    AtomAction,
    AtomProgram,
    AtomStep,
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
    Wait,
)
from .compiler import CompilationResult, CompiledOperation, MeasurementRecord
from .contracts import GateLayer
from .coverage import RegionSet
from .device import Device, Position, Region
from .physical_ir import ResultRef
from .state import AODGrid, AODHolder, AODLine, AtomLayout, AtomState, SLMHolder

RESULT_SCHEMA = "nadit.result/1"
_ACTION_CLASSES: dict[str, type[AtomAction]] = {
    action.__name__: action
    for action in (
        Load,
        Move,
        Store,
        CZPulse,
        Rotation,
        LocalU,
        GlobalRY,
        Reset,
        Measure,
        Wait,
    )
}


def _arrays(value: Any) -> Any:
    """Normalize dataclass tuple fields into JSON arrays without losing order."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _arrays(getattr(value, item.name))
            for item in fields(value)
            if item.init
        }
    if isinstance(value, dict):
        return {key: _arrays(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_arrays(item) for item in value]
    return value


def _coverage(value: Region | RegionSet | tuple[int, ...]) -> dict[str, Any]:
    if isinstance(value, Region):
        return {"kind": "region", "region": asdict(value)}
    if isinstance(value, RegionSet):
        return {"kind": "regions", "regions": [asdict(item) for item in value.regions]}
    return {"kind": "atoms", "atoms": list(value)}


def _read_coverage(value: Mapping[str, Any]) -> Region | RegionSet | tuple[int, ...]:
    kind = value.get("kind")
    if kind == "region":
        return Region(**value["region"])
    if kind == "regions":
        return RegionSet(tuple(Region(**item) for item in value["regions"]))
    if kind == "atoms":
        return tuple(value["atoms"])
    raise ValueError(f"Unknown coverage kind: {kind!r}.")


def _read_grid(value: Mapping[str, Any]) -> AODGrid:
    return AODGrid(
        value["aod_id"],
        tuple(AODLine(**line) for line in value["rows"]),
        tuple(AODLine(**line) for line in value["columns"]),
    )


def _action(action: AtomAction) -> dict[str, Any]:
    encoded: dict[str, Any] = {"kind": type(action).__name__, **_arrays(asdict(action))}
    if isinstance(action, CZPulse | GlobalRY):
        encoded["coverage"] = _coverage(action.coverage)
    return encoded


def _read_action(value: Mapping[str, Any]) -> AtomAction:
    kind = value.get("kind")
    action_class = _ACTION_CLASSES.get(kind) if isinstance(kind, str) else None
    if action_class is None:
        raise ValueError(f"Unknown action kind: {kind!r}.")
    fields = dict(value)
    del fields["kind"]
    fields["source_ids"] = tuple(fields["source_ids"])
    if kind in ("Load", "Move", "Store"):
        fields["grid"] = _read_grid(fields["grid"])
    if kind in ("Load", "Store"):
        fields["bindings"] = tuple(tuple(item) for item in fields["bindings"])
    if kind == "Move":
        fields["targets"] = tuple(
            (atom, Position(**position)) for atom, position in fields["targets"]
        )
    if kind in ("CZPulse", "GlobalRY"):
        fields["coverage"] = _read_coverage(fields["coverage"])
        if kind == "GlobalRY" and isinstance(fields["coverage"], tuple):
            raise ValueError("GlobalRY coverage must use a region or regions.")
    if kind in ("Rotation", "LocalU", "Reset"):
        fields["atoms"] = tuple(fields["atoms"])
    if kind == "Measure":
        fields["results"] = tuple(
            (atom, ResultRef(**result)) for atom, result in fields["results"]
        )
    return action_class(**fields)


def encode_program(program: AtomProgram) -> dict[str, Any]:
    """Return JSON fields for all steps, actions, timing, and global phase.

    Args:
        program: The executable Atom IR, without derived replay snapshots.

    Returns:
        A JSON object preserving action order and every Atom IR field.
    """
    return {
        "global_phase": program.global_phase,
        "steps": [
            {"actions": [_action(action) for action in step.actions]}
            for step in program.steps
        ],
    }


def decode_program(document: Mapping[str, Any]) -> AtomProgram:
    """Reconstruct equal Atom IR records from an encoded program.

    Args:
        document: The ``program`` member of a version-one result document.

    Returns:
        Immutable steps and typed actions.

    Raises:
        ValueError: If an action or coverage kind is unknown or values are invalid.
        KeyError: If a required field is missing.
        TypeError: If fields do not match an action's constructor.
    """
    return AtomProgram(
        tuple(
            AtomStep(tuple(_read_action(action) for action in step["actions"]))
            for step in document["steps"]
        ),
        document["global_phase"],
    )


def encode_layout(layout: AtomLayout) -> dict[str, Any]:
    """Return atom positions, typed holders, AOD grids, and qubit bindings.

    Args:
        layout: A compiler or replay layout, including unoccupied AOD lines.

    Returns:
        JSON fields with stable atom, line, and site identities.
    """
    return {
        "atoms": [
            {
                "id": atom.id,
                "position": asdict(atom.position),
                "holder": {
                    "kind": "slm" if isinstance(atom.holder, SLMHolder) else "aod",
                    **asdict(atom.holder),
                },
            }
            for atom in layout.atoms
        ],
        "aod_grids": [_arrays(asdict(grid)) for grid in layout.aod_grids],
        "qubit_to_atom": list(layout.qubit_to_atom),
    }


def _read_holder(value: Mapping[str, Any]) -> SLMHolder | AODHolder:
    fields = dict(value)
    kind = fields.pop("kind", None)
    if kind == "slm":
        return SLMHolder(**fields)
    if kind == "aod":
        return AODHolder(**fields)
    raise ValueError(f"Unknown holder kind: {kind!r}.")


def decode_layout(document: Mapping[str, Any]) -> AtomLayout:
    """Reconstruct an equal layout from encoded atom and grid records.

    Args:
        document: An ``entry_state`` or ``exit_state`` JSON object.

    Returns:
        Atom positions, holders, AOD grids, and ordered physical-qubit binding.

    Raises:
        ValueError: If a holder kind is unknown or layout values are invalid.
        KeyError: If a required field is missing.
        TypeError: If fields do not match record constructors.
    """
    return AtomLayout(
        tuple(
            AtomState(
                atom["id"], Position(**atom["position"]), _read_holder(atom["holder"])
            )
            for atom in document["atoms"]
        ),
        tuple(_read_grid(grid) for grid in document["aod_grids"]),
        tuple(document["qubit_to_atom"]),
    )


def encode_device(device: Device) -> dict[str, Any]:
    """Return an expanded public Device snapshot accepted by ``loads_device``.

    Args:
        device: Geometry, transport timing, and gate capabilities.

    Returns:
        JSON fields containing explicit SLM sites and excluding lookup caches.
    """
    result: dict[str, Any] = _arrays(device)
    return result


def _package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def collect_provenance(job: Mapping[str, Any]) -> dict[str, Any]:
    """Return software versions, canonical job hash, and available git revision.

    Args:
        job: Resolved JSON job whose canonical representation is hashed.

    Returns:
        Provenance without timestamps; unavailable package or git data is null.

    Raises:
        TypeError: If the job contains values that cannot be represented as JSON.
        ValueError: If the job contains nonfinite numbers.
    """
    canonical = json.dumps(job, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        git_commit = None
    return {
        "package_version": _package_version("nadit-physical-compiler"),
        "git_commit": git_commit,
        "job_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "python_version": platform.python_version(),
        "qiskit_version": _package_version("qiskit"),
    }


def encode_result(
    result: CompilationResult,
    *,
    entry_state: AtomLayout,
    job: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a version-one result document without replaying or recompiling.

    Args:
        result: Completed case-specific compilation.
        entry_state: Layout supplied to the compiler.
        job: Resolved job with defaults filled and paths made absolute.
        provenance: Explicit provenance, or collect local software versions.
        metrics: Optional metric snapshot supplied by the metrics module.
        validation: Optional reference-validation report; omitted when absent.

    Returns:
        A JSON result document containing lossless program and result records.
    """
    document = {
        "schema": RESULT_SCHEMA,
        "job": dict(job),
        "provenance": dict(provenance)
        if provenance is not None
        else collect_provenance(job),
        "program": encode_program(result.program),
        "entry_state": encode_layout(entry_state),
        "exit_state": encode_layout(result.exit_state),
        "operations": [asdict(item) for item in result.operations],
        "measurements": [asdict(item) for item in result.measurements],
        "layers": [_arrays(asdict(item)) for item in result.layers],
        "metrics": dict(metrics) if metrics is not None else {},
    }
    if validation is not None:
        document["validation"] = dict(validation)
    return document


def decode_result(document: Mapping[str, Any]) -> CompilationResult:
    """Reconstruct compilation records from a version-one result document.

    Args:
        document: JSON object with schema ``nadit.result/1``.

    Returns:
        The original CompilationResult; entry state is decoded separately.

    Raises:
        ValueError: If the schema, action kind, coverage, or layout is invalid.
        KeyError: If a required result field is missing.
        TypeError: If fields do not match record constructors.
    """
    if document.get("schema") != RESULT_SCHEMA:
        raise ValueError(f"Unsupported result schema: {document.get('schema')!r}.")
    return CompilationResult(
        decode_program(document["program"]),
        decode_layout(document["exit_state"]),
        tuple(CompiledOperation(**item) for item in document["operations"]),
        tuple(
            MeasurementRecord(
                ResultRef(**item["result"]), item["value"], item["available_at_us"]
            )
            for item in document["measurements"]
        ),
        tuple(GateLayer(tuple(item["operation_ids"])) for item in document["layers"]),
    )
