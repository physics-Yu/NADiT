"""Compile a multi-CZ circuit with partner changes and measurement feedback."""

import argparse
import json
from collections import Counter
from pathlib import Path

from nadit_physical_compiler import (
    AtomLayout,
    AtomState,
    CZPulse,
    FakeBackend,
    Load,
    Move,
    SLMHolder,
    Store,
    compile_physical,
    load_device,
    load_qasm,
    replay,
)
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import GatePairCatalog, MultiPairPlacer
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import SequentialTransport, StrictTransport


def main() -> None:
    """Print a configured case's metrics and complete Stim-style atom program."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bit", type=int, choices=(0, 1))
    parser.add_argument("--capacity", type=int, choices=(1, 2), default=2)
    parser.add_argument(
        "--transport", choices=("sequential", "strict"), default="sequential"
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Retain matched atoms for the next CZ layer.",
    )
    args = parser.parse_args()
    module = Path(__file__).resolve().parents[1]
    device = load_device(module / "configs/multipair_flexible.json")
    dag = load_qasm(module / "examples/multipair_feedback.qasm3")
    pairs = (("gate_0_left", "gate_0_right"), ("gate_1_left", "gate_1_right"))
    catalog = GatePairCatalog.from_device(
        device, "entanglement", pairs[: args.capacity]
    )
    entry = AtomLayout(
        tuple(
            AtomState(
                7 + q * 3,
                device.site(f"storage_r0_c{q}").position,
                SLMHolder(f"storage_r0_c{q}"),
            )
            for q in range(dag.num_qubits)
        ),
        qubit_to_atom=tuple(7 + q * 3 for q in range(dag.num_qubits)),
    )
    overrides = {
        reference: args.bit
        for node in dag.nodes
        if (reference := node.result) is not None and args.bit is not None
    }
    transporter = (
        StrictTransport("transport")
        if args.transport == "strict"
        else SequentialTransport("transport")
    )
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_cz_gates=catalog.capacity),
            MultiPairPlacer("transport", catalog),
            transporter,
            WholeLayerEmitter(catalog.coverage),
            reuse_analyzer=MatchingReuseAnalyzer() if args.reuse else None,
        ),
        entry_state=entry,
        measurement_backend=FakeBackend(args.seed, overrides),
    )
    assert replay(result.program, entry, device) == result.exit_state
    actions = [action for step in result.program.steps for action in step.actions]
    print(
        json.dumps(
            {
                "native_nodes": len(dag.nodes),
                "pair_capacity": catalog.capacity,
                "transport": args.transport,
                "reuse": args.reuse,
                "cz_gates_per_pulse": [
                    len(action.source_ids)
                    for action in actions
                    if isinstance(action, CZPulse)
                ],
                "atom_steps": len(result.program.steps),
                "action_counts": dict(
                    Counter(type(action).__name__ for action in actions)
                ),
                "load_batch_sizes": [
                    len(action.bindings)
                    for action in actions
                    if isinstance(action, Load)
                ],
                "max_parallel_atoms": max(
                    (
                        len(action.targets)
                        for action in actions
                        if isinstance(action, Move)
                    ),
                    default=0,
                ),
                "transport_duration_us": sum(
                    action.duration_us
                    for action in actions
                    if isinstance(action, Load | Move | Store)
                ),
                "atom_load_count": sum(
                    len(action.bindings)
                    for action in actions
                    if isinstance(action, Load)
                ),
                "program_duration_us": result.program.duration_us,
                "measurements": [
                    {
                        "source_id": item.result.measurement_id,
                        "value": item.value,
                        "available_at_us": item.available_at_us,
                    }
                    for item in result.measurements
                ],
                "skipped_native_ids": [
                    item.operation_id
                    for item in result.operations
                    if item.status == "skipped"
                ],
                "replay_matches_exit_state": True,
                "exit_slm_sites": {
                    str(atom.id): atom.holder.site_id
                    for atom in result.exit_state.atoms
                    if isinstance(atom.holder, SLMHolder)
                },
            },
            indent=2,
        )
    )
    print(result.program.to_text(), end="")


if __name__ == "__main__":
    main()
