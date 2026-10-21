"""Load a declarative compilation job and compose the existing Python pipeline."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .architectures import generate
from .compiler import CompilationResult, compile_physical
from .device import Device, load_device, loads_device
from .execution import FakeBackend
from .frontend import DEFAULT_GATE_SET, GateSet, loads_qasm
from .job_fields import array_at, integer_at, keys_at, object_at, required, string_at
from .physical_ir import PhysicalDAG, ResultRef
from .placement import GatePairCatalog, make_initial_layout
from .provenance import canonical_hash
from .registry import StrategyContext, build_strategy
from .serialization import encode_device
from .state import AtomLayout, AtomState, SLMHolder, validate_layout
from .strategies import SearchSmarterStrategy
from .support import UnsupportedJob

JOB_SCHEMA = "nadit.job/1"


@dataclass(frozen=True, slots=True)
class CompilationJob:
    """Resolved inputs ready for compile_physical, plus their saved JSON form.

    The compiler continues to own DAG progress and measurement feedback.
    Loading a job neither compiles operations nor samples measurements.
    """

    document: dict[str, Any]
    dag: PhysicalDAG
    device: Device
    catalog: GatePairCatalog | None
    entry_state: AtomLayout
    strategy: SearchSmarterStrategy
    measurement_backend: FakeBackend
    inputs: dict[str, str] = field(default_factory=dict)


def load_job(path: str | Path) -> CompilationJob:
    """Read a UTF-8 job; relative paths resolve against its parent directory.

    Args:
        path: Saved nadit.job/1 JSON file.
    Returns:
        Resolved job and assembled public compiler objects.
    Raises:
        ValueError: Invalid job or unreadable input, with its path.
        UnsupportedJob: A selected strategy or entry policy cannot handle the job.
    """
    path = Path(path).resolve()
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"job {path}: {error}") from error
    return loads_job(source, base_dir=path.parent)


def loads_job(source: str, *, base_dir: str | Path) -> CompilationJob:
    """Parse job JSON and construct its existing pipeline stages.

    Args:
        source: JSON text using schema nadit.job/1.
        base_dir: Directory against which file references are resolved.
    Returns:
        A compilation job with defaults filled and absolute input paths.
    Raises:
        ValueError: Invalid fields or source inputs, with their job paths.
        UnsupportedJob: A selected strategy or entry policy cannot handle the job.
    """
    try:
        data = object_at(json.loads(source), "job")
    except json.JSONDecodeError as error:
        raise ValueError(f"job: {error}") from error
    keys_at(
        data,
        {
            "schema",
            "circuit",
            "device",
            "aod",
            "gate_zones",
            "strategy",
            "entry",
            "case",
        },
        "job",
    )
    if data.get("schema") != JOB_SCHEMA:
        raise ValueError(f"schema: expected {JOB_SCHEMA!r}.")
    base = Path(base_dir).resolve()
    dag, circuit, qasm_hash = _circuit(required(data, "circuit", "job"), base)
    device, device_document = _device(required(data, "device", "job"), base)
    aod = string_at(required(data, "aod", "job"), "aod")
    try:
        device.aod(aod)
    except KeyError as error:
        raise ValueError(f"aod: unknown AOD {aod!r}.") from error
    catalog, zones = _catalog(
        data.get(
            "gate_zones",
            [
                {"zone": array.zone, "pairs": [list(pair) for pair in array.pairs]}
                for array in device.pair_arrays
            ],
        ),
        device,
    )
    strategy, strategy_document = build_strategy(
        required(data, "strategy", "job"),
        dag=dag,
        device=device,
        aod_id=aod,
        catalog=catalog,
    )
    context = StrategyContext(dag, device, aod, catalog, strategy_document["placement"])
    entry, entry_document = _entry(required(data, "entry", "job"), context)
    backend, case = _case(data.get("case", {}), dag)
    document = {
        "schema": JOB_SCHEMA,
        "circuit": circuit,
        "device": device_document,
        "aod": aod,
        "gate_zones": zones,
        "strategy": strategy_document,
        "entry": entry_document,
        "case": case,
    }
    inputs = {
        "qasm_sha256": qasm_hash,
        "device_sha256": canonical_hash(encode_device(device)),
    }
    return CompilationJob(
        document, dag, device, catalog, entry, strategy, backend, inputs
    )


def compile_job(job: CompilationJob) -> CompilationResult:
    """Compile a loaded job; compiler errors propagate without reclassification.

    Args:
        job: Inputs already resolved by load_job or loads_job.
    Returns:
        The unchanged public compiler result.
    """
    return compile_physical(
        job.dag,
        job.device,
        strategy=job.strategy,
        entry_state=job.entry_state,
        measurement_backend=job.measurement_backend,
    )


def _circuit(value: Any, base: Path) -> tuple[PhysicalDAG, dict[str, Any], str]:
    data = object_at(value, "circuit")
    keys_at(data, {"qasm", "gate_set"}, "circuit")
    path = (
        base / string_at(required(data, "qasm", "circuit"), "circuit.qasm")
    ).resolve()
    names = [
        string_at(name, f"circuit.gate_set[{index}]")
        for index, name in enumerate(
            array_at(
                data.get("gate_set", list(DEFAULT_GATE_SET.names)), "circuit.gate_set"
            )
        )
    ]
    try:
        source = path.read_bytes()
        dag = loads_qasm(source.decode("utf-8-sig"), gate_set=GateSet(tuple(names)))
    except (ValueError, OSError, UnicodeError) as error:
        raise ValueError(f"circuit.qasm {path}: {error}") from error
    return (
        dag,
        {"qasm": str(path), "gate_set": names},
        hashlib.sha256(source).hexdigest(),
    )


def _device(value: Any, base: Path) -> tuple[Device, str | dict[str, Any]]:
    try:
        if isinstance(value, str):
            path = (base / value).resolve()
            return load_device(path), str(path)
        data = object_at(value, "device")
        if "architecture" in data:
            keys_at(data, {"architecture"}, "device")
            spec = object_at(data["architecture"], "device.architecture")
            keys_at(spec, {"family", "params"}, "device.architecture")
            family = string_at(
                required(spec, "family", "device.architecture"),
                "device.architecture.family",
            )
            params = object_at(
                required(spec, "params", "device.architecture"),
                "device.architecture.params",
            )
            return loads_device(json.dumps(generate(family, params))), data
        return loads_device(json.dumps(data)), data
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise ValueError(f"device: {error}") from error


def _catalog(
    value: Any, device: Device
) -> tuple[GatePairCatalog | None, list[dict[str, Any]]]:
    zones: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    document: list[dict[str, Any]] = []
    for index, raw in enumerate(array_at(value, "gate_zones")):
        path = f"gate_zones[{index}]"
        item = object_at(raw, path)
        keys_at(item, {"zone", "pairs"}, path)
        name = string_at(required(item, "zone", path), f"{path}.zone")
        pairs: list[tuple[str, str]] = []
        for pair_index, raw_pair in enumerate(
            array_at(required(item, "pairs", path), f"{path}.pairs")
        ):
            pair_path = f"{path}.pairs[{pair_index}]"
            pair = array_at(raw_pair, pair_path)
            if len(pair) != 2:
                raise ValueError(f"{pair_path}: expected two site IDs.")
            pairs.append(
                (
                    string_at(pair[0], f"{pair_path}[0]"),
                    string_at(pair[1], f"{pair_path}[1]"),
                )
            )
        zones.append((name, tuple(pairs)))
        document.append({"zone": name, "pairs": [list(pair) for pair in pairs]})
    try:
        return (
            GatePairCatalog.from_zones(device, tuple(zones)) if zones else None
        ), document
    except (ValueError, KeyError) as error:
        raise ValueError(f"gate_zones: {error}") from error


def _entry(value: Any, context: StrategyContext) -> tuple[AtomLayout, dict[str, Any]]:
    data = object_at(value, "entry")
    kind = string_at(required(data, "kind", "entry"), "entry.kind")
    try:
        if kind == "initial_storage":
            keys_at(data, {"kind", "atom_ids"}, "entry")
            raw_ids = data.get("atom_ids")
            ids = (
                None
                if raw_ids is None
                else tuple(
                    integer_at(item, f"entry.atom_ids[{index}]")
                    for index, item in enumerate(array_at(raw_ids, "entry.atom_ids"))
                )
            )
            try:
                geometry = context.geometry()
            except (ValueError, KeyError, IndexError) as error:
                raise UnsupportedJob(
                    [
                        {
                            "stage": "entry",
                            "kind": kind,
                            "reason": f"cannot infer initial storage arrays: {error}",
                        }
                    ]
                ) from error
            capacity = sum(
                array.n_rows * array.n_cols for array in geometry.storage_arrays
            )
            if context.dag.num_qubits > capacity:
                raise UnsupportedJob(
                    [
                        {
                            "stage": "entry",
                            "kind": kind,
                            "reason": (
                                f"needs {context.dag.num_qubits} storage sites; "
                                f"{capacity} available"
                            ),
                        }
                    ]
                )
            layout = make_initial_layout(context.dag.num_qubits, geometry, atom_ids=ids)
            document = {"kind": kind, "atom_ids": None if ids is None else list(ids)}
        elif kind == "explicit":
            keys_at(data, {"kind", "atoms"}, "entry")
            layout = _explicit_entry(required(data, "atoms", "entry"), context)
            document = {"kind": kind, "atoms": data["atoms"]}
        else:
            raise ValueError(f"entry.kind: unknown kind {kind!r}.")
        validate_layout(context.device, layout)
    except UnsupportedJob:
        raise
    except (ValueError, KeyError, IndexError) as error:
        raise ValueError(f"entry: {error}") from error
    return layout, document


def _explicit_entry(value: Any, context: StrategyContext) -> AtomLayout:
    atoms: list[AtomState] = []
    mapping: dict[int, int] = {}
    for index, raw in enumerate(array_at(value, "entry.atoms")):
        path = f"entry.atoms[{index}]"
        triple = array_at(raw, path)
        if len(triple) != 3:
            raise ValueError(
                f"{path}: expected [atom ID, SLM site ID, physical qubit]."
            )
        atom = integer_at(triple[0], f"{path}[0]")
        site = context.device.site(string_at(triple[1], f"{path}[1]"))
        qubit = integer_at(triple[2], f"{path}[2]")
        if qubit in mapping:
            raise ValueError(f"{path}[2]: physical qubit is already bound.")
        mapping[qubit] = atom
        atoms.append(AtomState(atom, site.position, SLMHolder(site.id)))
    if set(mapping) != set(range(context.dag.num_qubits)):
        raise ValueError("entry.atoms: bind each physical qubit exactly once.")
    return AtomLayout(
        tuple(atoms),
        qubit_to_atom=tuple(mapping[index] for index in range(context.dag.num_qubits)),
    )


def _case(value: Any, dag: PhysicalDAG) -> tuple[FakeBackend, dict[str, Any]]:
    data = object_at(value, "case")
    keys_at(data, {"seed", "overrides"}, "case")
    seed = integer_at(data.get("seed", 0), "case.seed")
    raw = object_at(data.get("overrides", {}), "case.overrides")
    available = {
        str(node.result.measurement_id): node.result
        for node in dag.nodes
        if node.result is not None
    }
    overrides: dict[ResultRef, int] = {}
    for key, value in raw.items():
        path = f"case.overrides.{key}"
        bit = integer_at(value, path)
        if key not in available:
            raise ValueError(f"{path}: unknown source measurement ID.")
        if bit not in (0, 1):
            raise ValueError(f"{path}: expected 0 or 1.")
        overrides[available[key]] = bit
    return FakeBackend(seed, overrides), {"seed": seed, "overrides": raw}
