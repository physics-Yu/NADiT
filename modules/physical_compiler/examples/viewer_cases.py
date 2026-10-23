"""Compile reproducible circuits for the Atom IR replay viewer."""

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from nadit_physical_compiler import (
    AOD,
    AtomLayout,
    CompilationResult,
    CZCapability,
    Device,
    LocalControl,
    PhysicalDAG,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
    load_device,
    loads_qasm,
    replay,
    slm_grid,
)
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.serialization import encode_device, encode_result
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]
SEED = 17
STATIC_ATOMS = 60
STATIC_PAIRS = 12


@dataclass(frozen=True)
class CompiledExample:
    """One actual compilation with its inputs and replay entry state."""

    title: str
    description: str
    source_qasm: str
    config: dict[str, Any]
    dag: PhysicalDAG
    device: Device
    entry: AtomLayout
    result: CompilationResult
    document: dict[str, Any]


def _compile(
    title: str,
    description: str,
    source_qasm: str,
    device: Device,
    pairs: tuple[tuple[str, str], ...],
    bit: int | None = None,
) -> CompiledExample:
    dag = loads_qasm(source_qasm)
    overrides = {
        str(node.result.measurement_id): bit
        for node in dag.nodes
        if node.result is not None and bit is not None
    }
    with TemporaryDirectory(prefix="nadit-viewer-example-") as directory:
        qasm = Path(directory) / "circuit.qasm3"
        qasm.write_text(source_qasm, encoding="utf-8")
        specification = {
            "schema": "nadit.job/1",
            "circuit": {"qasm": str(qasm)},
            "device": encode_device(device),
            "aod": "transport",
            "gate_zones": [{"zone": "entanglement", "pairs": pairs}],
            "strategy": {
                "kind": "search_smarter",
                "scheduler": {"kind": "ready", "max_cz_gates": "catalog"},
                "placement": {"kind": "ids"},
                "transport": {"kind": "relaxed"},
                "emitter": {"kind": "whole_layer"},
                "reuse": {"kind": "matching"},
            },
            "entry": {
                "kind": "initial_storage",
                "atom_ids": [7 + q * 3 for q in range(dag.num_qubits)],
            },
            "case": {"seed": SEED, "overrides": overrides},
        }
        job = loads_job(json.dumps(specification), base_dir=directory)
        result = compile_job(job)
    validation = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    if not validation["ok"]:
        raise AssertionError(f"{title}: {validation}")
    if replay(result.program, job.entry_state, job.device) != result.exit_state:
        raise AssertionError(f"{title}: Atom IR replay differs from compiler exit.")
    document = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
        validation=validation,
    )
    metadata = {
        key: document[key]
        for key in ("job", "provenance", "metrics", "validation", "inputs")
    }
    return CompiledExample(
        title.replace("|", "-"),
        description,
        source_qasm,
        metadata,
        job.dag,
        job.device,
        job.entry_state,
        result,
        document,
    )


def compile_feedback(bit: int) -> CompiledExample:
    """Compile the checked-in feedback circuit with one forced measurement bit.

    Args:
        bit: FakeBackend result, either zero or one.

    Returns:
        Compiler inputs, result, and reproducible configuration.

    Raises:
        ValueError: If the measurement value is not a bit.
    """
    if bit not in (0, 1):
        raise ValueError("The feedback example requires measurement bit 0 or 1.")
    return _compile(
        f"Feedback branch {bit} | 4 atoms",
        "Checked-in multipair_feedback.qasm3; IDS placement, matched reuse, "
        "relaxed staged transport. FakeBackend fixes the measured bit to "
        f"{bit}; feedback becomes available after the configured 500 us latency.",
        (MODULE / "examples/multipair_feedback.qasm3").read_text(encoding="utf-8"),
        load_device(MODULE / "configs/multipair_flexible.json"),
        (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right")),
        bit,
    )


def _static_device() -> tuple[Device, tuple[tuple[str, str], ...]]:
    pairs = tuple(
        (f"gate_{index}_left", f"gate_{index}_right") for index in range(STATIC_PAIRS)
    )
    gate_sites = tuple(
        SLMSite(site_id, Position(10 * index + 2 * side, 120))
        for index, pair in enumerate(pairs)
        for side, site_id in enumerate(pair)
    )
    device = Device(
        "viewer_static60",
        (
            Zone("storage", Region(0, 0, 55, 35), ("load", "store", "rotate")),
            Zone(
                "entanglement",
                Region(0, 120, 112, 120),
                ("load", "store", "rotate", "cz"),
            ),
        ),
        (*slm_grid("storage", Position(0, 0), 8, 12, Position(5, 5)), *gate_sites),
        (AOD("transport", Region(-10, -10, 130, 140), 1),),
        Timing(10, 10, 1),
        cz=CZCapability("rydberg", 4.6, 0.5),
        local=LocalControl("raman", 1, 5),
    )
    return device, pairs


def compile_static() -> CompiledExample:
    """Compile a local 60-atom ring-and-chord circuit on twelve gate pairs.

    Returns:
        Actual compiler actions and inputs; this is not a paper benchmark case.
    """
    rounds = (
        tuple((qubit, qubit + 1) for qubit in range(0, STATIC_ATOMS, 2)),
        tuple(
            (qubit, (qubit + 1) % STATIC_ATOMS) for qubit in range(1, STATIC_ATOMS, 2)
        ),
        tuple((qubit, qubit + STATIC_ATOMS // 2) for qubit in range(STATIC_ATOMS // 2)),
    )
    statements = [
        'OPENQASM 3.0; include "stdgates.inc";',
        f"qubit[{STATIC_ATOMS}] q;",
        "h q;",
    ]
    for pairs in rounds:
        statements.append("barrier q;")
        statements.extend(f"cz q[{first}], q[{second}];" for first, second in pairs)
    device, gate_pairs = _static_device()
    return _compile(
        "Parallel transport | 60 atoms",
        "A local demonstration: a 60-qubit ring plus 30 cross-ring edges "
        "(90 CZ operations), with twelve gate pairs. Storage pitch is 5 um; "
        "gate-pair separation is 2 um; interaction radius is 4.6 um. "
        "IDS placement, matched reuse, and relaxed staged transport.",
        "\n".join(statements) + "\n",
        device,
        gate_pairs,
    )


def compile_examples() -> tuple[CompiledExample, ...]:
    """Return the two feedback branches and one larger static compilation."""
    return compile_feedback(0), compile_feedback(1), compile_static()
