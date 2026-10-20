"""Separate the original endpoint-priority policy from complete shared edges."""

from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest
from test_ids_compiler import _strategy
from test_reuse import Pairs, _circuit
from test_strict_compiler import RecordingBackend, _audit

from nadit_physical_compiler.compiler import compile_physical
from nadit_physical_compiler.device import load_device
from nadit_physical_compiler.frontend import load_qasm
from nadit_physical_compiler.placement import HeuristicPlacer
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer, SourceReuseAnalyzer
from nadit_physical_compiler.reuse.source import source_matching
from nadit_physical_compiler.transport import RelaxedTransport, StagedStrictTransport


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ((), (), ()),
        (((0, 1),), (), ()),
        ((), ((0, 1),), ()),
        (((0, 1),), ((2, 3),), ()),
        (((0, 1),), ((1, 0),), (0, 1)),
        (((0, 1), (2, 3)), ((0, 2), (1, 3)), (0,)),
        (((0, 1), (2, 3)), ((2, 4), (0, 5)), (0, 2)),
        (((1, 2),), ((0, 2),), (2,)),
    ],
)
def test_source_reuse_preserves_endpoint_priority(
    before: Pairs, after: Pairs, expected: tuple[int, ...]
) -> None:
    assert SourceReuseAnalyzer().analyze(*_circuit(before, after)) == expected


def test_original_and_complete_edge_reuse_are_explicitly_different_policies() -> None:
    circuit = _circuit(((0, 1), (2, 3)), ((0, 2), (1, 3)))
    assert SourceReuseAnalyzer().analyze(*circuit) == (0,)
    assert MatchingReuseAnalyzer().analyze(*circuit) == (1, 2)


def test_source_bfs_dfs_uses_an_augmenting_path() -> None:
    assert source_matching(((0, 1), (0,))) == (1, 0)
    assert source_matching(((), ())) == (None, None)
    assert source_matching(()) == ()


def _maximum_cardinality(adjacency: tuple[tuple[int, ...], ...]) -> int:
    options = tuple((None, *row) for row in adjacency)
    return max(
        (
            len(sinks)
            for assignment in product(*options)
            if len(sinks := [sink for sink in assignment if sink is not None])
            == len(set(sinks))
        ),
        default=0,
    )


def test_source_matching_matches_all_small_graph_cardinalities() -> None:
    for bits in product((False, True), repeat=9):
        adjacency = tuple(
            tuple(column for column in range(3) if bits[3 * row + column])
            for row in range(3)
        )
        matching = source_matching(adjacency)
        assert sum(sink is not None for sink in matching) == _maximum_cardinality(
            adjacency
        )
        assert all(
            sink is None or sink in adjacency[source]
            for source, sink in enumerate(matching)
        )


@pytest.mark.parametrize("bit", [0, 1])
@pytest.mark.parametrize("relaxed", [False, True])
def test_source_reuse_policy_preserves_online_feedback_and_action_replay(
    bit: int, relaxed: bool
) -> None:
    module = Path(__file__).parents[1]
    device = load_device(module / "configs/multipair_flexible.json")
    dag = load_qasm(module / "examples/multipair_feedback.qasm3")
    strategy = _strategy(device)
    assert isinstance(strategy.placer, HeuristicPlacer)
    entry = strategy.placer.initial_layout(dag.num_qubits, device)
    strategy = replace(
        strategy,
        reuse_analyzer=SourceReuseAnalyzer(),
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
