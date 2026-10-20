"""Maximum reuse matching across disjoint CZ layers."""

from collections.abc import Iterator
from itertools import product

import pytest

from nadit_physical_compiler.contracts import GateLayer, ReuseAnalyzer
from nadit_physical_compiler.physical_ir import PhysicalDAG, PhysicalOperation
from nadit_physical_compiler.reuse import MatchingReuseAnalyzer

type Pairs = tuple[tuple[int, int], ...]


def _circuit(
    current: Pairs, following: Pairs
) -> tuple[PhysicalDAG, GateLayer, GateLayer]:
    pairs = current + following
    num_qubits = max((qubit for pair in pairs for qubit in pair), default=-1) + 1
    dag = PhysicalDAG(
        num_qubits,
        0,
        tuple(
            PhysicalOperation(index, "cz", pair, (), ())
            for index, pair in enumerate(pairs)
        ),
        (),
    )
    return (
        dag,
        GateLayer(tuple(range(len(current)))),
        GateLayer(tuple(range(len(current), len(pairs)))),
    )


@pytest.mark.parametrize(
    ("current", "following"),
    [
        ((), ()),
        (((0, 1),), ()),
        ((), ((0, 1),)),
        (((0, 1),), ((2, 3),)),
    ],
)
def test_empty_or_disjoint_layers_have_no_reuse(
    current: Pairs, following: Pairs
) -> None:
    assert MatchingReuseAnalyzer().analyze(*_circuit(current, following)) == ()


def test_identical_gate_with_reversed_operands_retains_both_endpoints() -> None:
    analyzer: ReuseAnalyzer = MatchingReuseAnalyzer()
    circuit = _circuit(((0, 1), (2, 3)), ((1, 0), (2, 4)))

    assert analyzer.analyze(*circuit) == (0, 1, 2)


def test_changed_partners_form_a_matching_using_both_old_gate_endpoints() -> None:
    circuit = _circuit(((0, 1), (2, 3)), ((0, 2), (1, 3)))

    assert MatchingReuseAnalyzer().analyze(*circuit) == (1, 2)


def test_augmenting_path_reassigns_an_earlier_greedy_choice() -> None:
    # Old gate 0 reaches both next gates, but old gate 1 reaches only next gate 0.
    circuit = _circuit(((0, 1), (2, 3)), ((0, 2), (1, 4)))

    assert MatchingReuseAnalyzer().analyze(*circuit) == (1, 2)


def test_unequal_layers_reuse_one_endpoint_per_changed_partner_gate() -> None:
    circuit = _circuit(((0, 1), (2, 3), (4, 5)), ((0, 2), (4, 6)))

    assert MatchingReuseAnalyzer().analyze(*circuit) == (0, 4)


def test_tie_breaking_is_stable_under_layer_input_order() -> None:
    dag, current, following = _circuit(((0, 1), (2, 3)), ((0, 2), (1, 3)))
    analyzer = MatchingReuseAnalyzer()
    expected = analyzer.analyze(dag, current, following)

    assert expected == (1, 2)
    assert (
        analyzer.analyze(
            dag,
            GateLayer(tuple(reversed(current.operation_ids))),
            GateLayer(tuple(reversed(following.operation_ids))),
        )
        == expected
    )
    assert analyzer.analyze(dag, current, following) == expected


def test_non_cz_operations_are_ignored_and_outputs_are_physical_qubits() -> None:
    dag = PhysicalDAG(
        8,
        0,
        (
            PhysicalOperation(0, "rx", (7,), (), (0.5,)),
            PhysicalOperation(1, "cz", (4, 5), (), (), source_id=100),
            PhysicalOperation(2, "reset", (0,), (), ()),
            PhysicalOperation(3, "cz", (5, 6), (), (), source_id=200),
        ),
        (),
    )
    analyzer = MatchingReuseAnalyzer()

    assert analyzer.analyze(dag, GateLayer((0, 1)), GateLayer((2, 3))) == (5,)
    assert analyzer.analyze(dag, GateLayer((0,)), GateLayer((2,))) == ()


def _pairings(qubits: tuple[int, ...]) -> Iterator[Pairs]:
    """Enumerate all partial pairings of a small set of distinct qubits."""
    if not qubits:
        yield ()
        return
    first, *rest = qubits
    yield from _pairings(tuple(rest))
    for index, partner in enumerate(rest):
        remaining = tuple(rest[:index] + rest[index + 1 :])
        for pairing in _pairings(remaining):
            yield ((first, partner), *pairing)


def _oracle(
    current: Pairs, following: Pairs, used: frozenset[int] = frozenset()
) -> int:
    """Exhaustively maximize retained endpoints over all gate matchings."""
    if not current:
        return 0
    first, *rest = current
    best = _oracle(tuple(rest), following, used)
    for index, second in enumerate(following):
        shared = set(first) & set(second)
        if index not in used and shared:
            best = max(
                best,
                len(shared) + _oracle(tuple(rest), following, used | {index}),
            )
    return best


def test_all_small_layer_pairs_match_an_independent_cardinality_oracle() -> None:
    layers = tuple(_pairings(tuple(range(6))))
    analyzer = MatchingReuseAnalyzer()

    for current, following in product(layers, repeat=2):
        actual = analyzer.analyze(*_circuit(current, following))
        assert len(actual) == _oracle(current, following), (current, following, actual)
        shared_qubits = {qubit for pair in current for qubit in pair} & {
            qubit for pair in following for qubit in pair
        }
        assert set(actual) <= shared_qubits
        assert actual == tuple(sorted(set(actual)))
        for layer, other in ((current, following), (following, current)):
            for pair in layer:
                count = len(set(pair) & set(actual))
                assert count <= (2 if set(pair) in map(set, other) else 1)


def test_long_augmenting_path_does_not_depend_on_python_recursion_depth() -> None:
    count = 1100
    current = tuple((2 * index, 2 * index + 1) for index in range(count))
    following = (
        (0, 2 * count - 2),
        *((2 * index - 1, 2 * index) for index in range(1, count - 1)),
        (2 * count - 3, 2 * count),
    )

    actual = MatchingReuseAnalyzer().analyze(*_circuit(current, following))

    assert actual == (*range(1, 2 * count - 2, 2), 2 * count - 2)
