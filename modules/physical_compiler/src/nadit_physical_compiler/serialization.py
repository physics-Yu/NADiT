"""Versioned JSON documents for executable Atom IR and compilation results."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
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
from .device import Device, Position, Region, loads_device
from .metrics import collect_metrics
from .physical_ir import Condition, PhysicalDAG, PhysicalOperation, Register, ResultRef
from .provenance import collect_provenance as collect_provenance
from .state import AODGrid, AODHolder, AODLine, AtomLayout, AtomState, SLMHolder

RESULT_SCHEMA = "nadit.result/1"
RESULT_SCHEMA_V2 = "nadit.result/2"
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


def encode_dag(dag: PhysicalDAG) -> dict[str, Any]:
    """Return native operations, dependencies, registers, and measurement identities.

    Args:
        dag: Device-independent native circuit used by the compiler.
    Returns:
        Lossless JSON fields, including each node's derived ResultRef.
    """
    result: dict[str, Any] = _arrays(dag)
    for encoded, node in zip(result["nodes"], dag.nodes, strict=True):
        encoded["result"] = _arrays(node.result)
    return result


def _read_operation(value: Mapping[str, Any], index: int) -> PhysicalOperation:
    encoded = dict(value)
    result = encoded.pop("result")
    for name in ("qubits", "clbits", "params"):
        encoded[name] = tuple(encoded[name])
    if encoded["condition"] is not None:
        condition = encoded["condition"]
        encoded["condition"] = Condition(
            ResultRef(**condition["result"]), condition["value"]
        )
    node = PhysicalOperation(**encoded)
    if result != _arrays(node.result):
        raise ValueError(
            f"dag.nodes[{index}].result disagrees with the measurement source_id."
        )
    return node


def decode_dag(document: Mapping[str, Any]) -> PhysicalDAG:
    """Reconstruct the native DAG without reading QASM or compiling.

    Args:
        document: The ``dag`` member of a version-two result document.
    Returns:
        Immutable PhysicalDAG records equal to the encoded circuit.
    Raises:
        ValueError: DAG invariants or a derived measurement identity are invalid.
        KeyError: A required field is missing.
        TypeError: Fields do not match the physical record constructors.
    """
    encoded = dict(document)
    encoded["nodes"] = tuple(
        _read_operation(node, index) for index, node in enumerate(encoded["nodes"])
    )
    encoded["edges"] = tuple(tuple(edge) for edge in encoded["edges"])
    for name in ("quantum_registers", "classical_registers"):
        encoded[name] = tuple(
            Register(register["name"], tuple(register["bits"]))
            for register in encoded[name]
        )
    return PhysicalDAG(**encoded)


def encode_result(
    result: CompilationResult,
    *,
    entry_state: AtomLayout,
    job: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    dag: PhysicalDAG | None = None,
    device: Device | None = None,
    inputs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a result snapshot without replaying or recompiling.

    Args:
        result: Completed case-specific compilation.
        entry_state: Layout supplied to the compiler.
        job: Resolved job with defaults filled and paths made absolute.
        provenance: Explicit provenance, or collect local software versions.
        metrics: Explicit metric snapshot, or compute the shared metric set.
        validation: Optional reference-validation report; omitted when absent.
        dag: Native circuit snapshot for a version-two document.
        device: Expanded Device snapshot for a version-two document.
        inputs: Captured content hashes; required together with dag and device.

    Returns:
        A JSON result document containing lossless program and result records.
        Nested job and metadata values are copied into an independent snapshot.
        Omitting all three snapshot arguments retains version-one serialization.
    Raises:
        ValueError: Only part of the version-two snapshot arguments is supplied.
    """
    snapshots = (dag is not None, device is not None, inputs is not None)
    if any(snapshots) and not all(snapshots):
        raise ValueError(
            "Version-two results require dag, device, and inputs together."
        )
    job_snapshot = deepcopy(dict(job))
    document: dict[str, Any] = {
        "schema": RESULT_SCHEMA_V2 if all(snapshots) else RESULT_SCHEMA,
        "job": job_snapshot,
        "provenance": deepcopy(dict(provenance))
        if provenance is not None
        else collect_provenance(job_snapshot, inputs=inputs),
        "program": encode_program(result.program),
        "entry_state": encode_layout(entry_state),
        "exit_state": encode_layout(result.exit_state),
        "operations": [asdict(item) for item in result.operations],
        "measurements": [asdict(item) for item in result.measurements],
        "layers": [_arrays(asdict(item)) for item in result.layers],
        "metrics": deepcopy(dict(metrics))
        if metrics is not None
        else collect_metrics(result),
    }
    if validation is not None:
        document["validation"] = deepcopy(dict(validation))
    if dag is not None and device is not None and inputs is not None:
        from .compilers.registry import identity as compiler_identity

        document["provenance"].setdefault(
            "compiler", compiler_identity(dict(job_snapshot.get("strategy", {})))
        )
        document["dag"] = encode_dag(dag)
        document["device"] = encode_device(device)
        document["inputs"] = deepcopy(dict(inputs))
    return document


def _check_v2(document: Mapping[str, Any]) -> None:
    try:
        for name in ("dag", "device", "inputs"):
            if name not in document:
                raise ValueError(f"missing {name} snapshot")
        decode_dag(document["dag"])
        loads_device(json.dumps(document["device"], allow_nan=False))
        for name in ("qasm_sha256", "device_sha256"):
            value = document["inputs"].get(name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(
                    f"inputs.{name}: expected a SHA-256 hexadecimal digest"
                )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(
            f"Invalid result schema {RESULT_SCHEMA_V2!r}: {error}"
        ) from error


def decode_result(document: Mapping[str, Any]) -> CompilationResult:
    """Reconstruct compilation records from a version-one or version-two document.

    Args:
        document: JSON object with schema ``nadit.result/1`` or ``nadit.result/2``.

    Returns:
        The original CompilationResult; entry state is decoded separately.

    Raises:
        ValueError: If the schema, action kind, coverage, or layout is invalid.
        KeyError: If a required result field is missing.
        TypeError: If fields do not match record constructors.
    """
    if document.get("schema") not in (RESULT_SCHEMA, RESULT_SCHEMA_V2):
        raise ValueError(f"Unsupported result schema: {document.get('schema')!r}.")
    if document["schema"] == RESULT_SCHEMA_V2:
        _check_v2(document)
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
