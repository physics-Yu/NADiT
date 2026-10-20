"""Compile and replay a two-atom circuit with one measurement-feedback branch."""

import argparse
import json
from pathlib import Path

from nadit_physical_compiler import (
    AtomLayout,
    AtomState,
    FakeBackend,
    SLMHolder,
    compile_physical,
    load_device,
    load_qasm,
    replay,
)
from nadit_physical_compiler.placement import PairPlacer
from nadit_physical_compiler.scheduling import ReadyScheduler
from nadit_physical_compiler.strategies import SearchSmarterStrategy
from nadit_physical_compiler.transport import PairTransport


def main() -> None:
    """Print compilation metrics and Stim-style actions for a seeded or forced case."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bit", type=int, choices=(0, 1))
    args = parser.parse_args()
    module = Path(__file__).resolve().parents[1]
    device = load_device(module / "configs/one_pair_flexible.json")
    dag = load_qasm(module / "examples/pair_feedback.qasm3")
    entry = AtomLayout(
        tuple(
            AtomState(atom, device.site(site).position, SLMHolder(site))
            for atom, site in ((7, "storage_r0_c0"), (11, "storage_r0_c1"))
        ),
        qubit_to_atom=(7, 11),
    )
    overrides = {
        reference: args.bit
        for node in dag.nodes
        if (reference := node.result) is not None and args.bit is not None
    }
    result = compile_physical(
        dag,
        device,
        strategy=SearchSmarterStrategy(
            ReadyScheduler(), PairPlacer("transport"), PairTransport("transport")
        ),
        entry_state=entry,
        measurement_backend=FakeBackend(args.seed, overrides),
    )
    assert replay(result.program, entry, device) == result.exit_state
    print(
        json.dumps(
            {
                "native_nodes": len(dag.nodes),
                "layers": len(result.layers),
                "atom_steps": len(result.program.steps),
                "program_duration_us": result.program.duration_us,
                "result_completion_us": result.duration_us,
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
            },
            indent=2,
        )
    )
    print(result.program.to_text(), end="")


if __name__ == "__main__":
    main()
