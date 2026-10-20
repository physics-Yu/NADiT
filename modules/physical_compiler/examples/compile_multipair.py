"""Compile a multi-CZ circuit with partner changes and measurement feedback."""

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from nadit_physical_compiler import (
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
from nadit_physical_compiler.contracts import Transporter
from nadit_physical_compiler.emission import WholeLayerEmitter
from nadit_physical_compiler.placement import (
    GatePairCatalog,
    HeuristicPlacer,
    MultiPairPlacer,
    PlacementConfig,
    PlacementGeometry,
    make_initial_layout,
)
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import (
    RelaxedTransport,
    SequentialTransport,
    StagedStrictTransport,
    StrictTransport,
)


def main() -> None:
    """Print a configured case's metrics and complete Stim-style atom program."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bit", type=int, choices=(0, 1))
    parser.add_argument("--capacity", type=int, choices=(1, 2), default=2)
    parser.add_argument("--placement", choices=("greedy", "ids"), default="greedy")
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--queue-capacity", type=int, default=100)
    parser.add_argument("--reuse-level", type=float, default=5.0)
    parser.add_argument("--window", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--window-min-width", type=int, default=16)
    parser.add_argument("--window-ratio", type=float, default=1.0)
    parser.add_argument("--window-share", type=float, default=0.8)
    parser.add_argument("--prefer-split", type=float, default=1.0)
    parser.add_argument(
        "--transport",
        choices=("sequential", "strict", "staged", "relaxed"),
        default="sequential",
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
    geometry = PlacementGeometry.from_device(device, "transport", catalog)
    entry = make_initial_layout(
        dag.num_qubits,
        geometry,
        atom_ids=tuple(7 + q * 3 for q in range(dag.num_qubits)),
    )
    overrides = {
        reference: args.bit
        for node in dag.nodes
        if (reference := node.result) is not None and args.bit is not None
    }
    transports: dict[str, Transporter] = {
        "sequential": SequentialTransport("transport"),
        "strict": StrictTransport("transport"),
        "staged": StagedStrictTransport("transport"),
        "relaxed": RelaxedTransport("transport", prefer_split=args.prefer_split),
    }
    transporter = transports[args.transport]
    placer = (
        HeuristicPlacer(
            "transport",
            catalog,
            config=PlacementConfig(
                trials=args.trials,
                queue_capacity=args.queue_capacity,
                reuse_level=args.reuse_level,
                use_window=args.window,
                window_min_width=args.window_min_width,
                window_ratio=args.window_ratio,
                window_share=args.window_share,
            ),
            geometry=geometry,
        )
        if args.placement == "ids"
        else MultiPairPlacer("transport", catalog)
    )
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(max_cz_gates=catalog.capacity),
            placer,
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
                "placement": args.placement,
                "initial_slm_sites": {
                    str(atom.id): atom.holder.site_id
                    for atom in entry.atoms
                    if isinstance(atom.holder, SLMHolder)
                },
                "placement_config": (
                    asdict(placer.config)
                    if isinstance(placer, HeuristicPlacer)
                    else None
                ),
                "transport": args.transport,
                "prefer_split": args.prefer_split
                if args.transport == "relaxed"
                else None,
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
                "atom_store_count": sum(
                    len(action.bindings)
                    for action in actions
                    if isinstance(action, Store)
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
