"""Source layout initialization and windows preserve the feedback pipeline."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_ids_compiler import _strategy
from test_strict_compiler import RecordingBackend, _audit

from nadit_physical_compiler.atom_ir import CZPulse
from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.device import load_device
from nadit_physical_compiler.frontend import load_qasm
from nadit_physical_compiler.placement import (
    HeuristicPlacer,
    PlacementConfig,
    PlacementGeometry,
    make_initial_layout,
)
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport

MODULE = Path(__file__).parents[1]


@pytest.mark.parametrize("bit", [0, 1])
@pytest.mark.parametrize("use_window", [False, True])
@pytest.mark.parametrize("relaxed", [False, True])
def test_automatic_initialization_and_source_candidates_complete_feedback_case(
    bit: int, use_window: bool, relaxed: bool
) -> None:
    device = load_device(MODULE / "configs/multipair_flexible.json")
    dag = load_qasm(MODULE / "examples/multipair_feedback.qasm3")
    strategy = _strategy(device)
    assert isinstance(strategy.placer, HeuristicPlacer)
    geometry = PlacementGeometry.from_device(
        device, "transport", strategy.placer.catalog
    )
    placer = replace(
        strategy.placer,
        geometry=geometry,
        config=PlacementConfig(
            use_window=use_window, window_min_width=0, window_share=1.0
        ),
    )
    # The atom IDs deliberately differ from physical-qubit order.
    ids = (47, 11, 89, 23)
    entry = placer.initial_layout(dag.num_qubits, device, atom_ids=ids)
    assert entry == make_initial_layout(dag.num_qubits, geometry, atom_ids=ids)
    assert entry.qubit_to_atom == ids
    assert {atom.position.y for atom in entry.atoms} == {10}
    strategy = replace(
        strategy,
        placer=placer,
        transporter=(
            RelaxedTransport("transport")
            if relaxed
            else StagedStrictTransport("transport")
        ),
    )
    backend = RecordingBackend(bit)

    result = compile_physical(
        dag,
        device,
        strategy=strategy,
        entry_state=entry,
        measurement_backend=backend,
    )

    _audit(dag, result, device, entry)
    assert backend.calls == [
        node.result for node in dag.nodes if node.result is not None
    ]
    assert [record.value for record in result.measurements] == [bit]
    assert sum(
        len(action.source_ids)
        for step in result.program.steps
        for action in step.actions
        if isinstance(action, CZPulse)
    ) == (6 if bit else 4)
    assert result.exit_state.qubit_to_atom == ids
