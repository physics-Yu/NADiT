"""Compile reproducible circuits for the Atom IR replay viewer."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nadit_physical_compiler import (
    AOD,
    AtomLayout,
    CompilationResult,
    CZCapability,
    Device,
    FakeBackend,
    LocalControl,
    PhysicalDAG,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
    compile_physical,
    load_device,
    loads_qasm,
    replay,
    slm_grid,
)
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    PlacementConfig,
    PlacementGeometry,
    make_initial_layout,
)
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import RelaxedTransport

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


def _compile(
    title: str,
    description: str,
    source_qasm: str,
    device: Device,
    pairs: tuple[tuple[str, str], ...],
    bit: int | None = None,
) -> CompiledExample:
    dag = loads_qasm(source_qasm)
    catalog = GatePairCatalog.from_device(device, "entanglement", pairs)
    geometry = PlacementGeometry.from_device(device, "transport", catalog)
    config = PlacementConfig()
    entry = make_initial_layout(
        dag.num_qubits,
        geometry,
        atom_ids=tuple(7 + qubit * 3 for qubit in range(dag.num_qubits)),
    )
    overrides = {
        reference: bit
        for node in dag.nodes
        if (reference := node.result) is not None and bit is not None
    }
    result = compile_physical(
        dag,
        device,
        entry_state=entry,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_cz_gates=catalog.capacity),
            HeuristicPlacer("transport", catalog, config=config, geometry=geometry),
            RelaxedTransport("transport"),
            WholeLayerEmitter(catalog.coverage),
            reuse_analyzer=MatchingReuseAnalyzer(),
        ),
        measurement_backend=FakeBackend(SEED, overrides),
    )
    if replay(result.program, entry, device) != result.exit_state:
        raise AssertionError(f"{title}: Atom IR replay differs from compiler exit.")
    return CompiledExample(
        title,
        description,
        source_qasm,
        {
            "scheduler": "ReadyScheduler",
            "placement": asdict(config),
            "transport": "RelaxedTransport",
            "prefer_split": 1.0,
            "reuse": "MatchingReuseAnalyzer",
            "pair_capacity": catalog.capacity,
            "measurement_seed": SEED,
            "measurement_overrides": {
                str(reference.measurement_id): value
                for reference, value in overrides.items()
            },
            "replay_matches_exit_state": True,
        },
        dag,
        device,
        entry,
        result,
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
