"""Source float storage and ordered accumulation boundaries."""

from nadit_physical_compiler.placement.arithmetic import f32, sum32
from nadit_physical_compiler.placement.scoring import _Node, _Search


def test_float_rounds_like_source_configuration_fields() -> None:
    assert f32(0.4) == 0.4000000059604645
    assert f32(0.9) == 0.8999999761581421


def test_sum_rounds_each_addition_in_iteration_order() -> None:
    assert sum32((16777216.0, 1.0, -16777216.0)) == 0.0
    assert sum32((16777216.0, -16777216.0, 1.0)) == 1.0
    assert sum32((), start=0.4) == f32(0.4)


def test_group_cost_accumulates_after_lookahead_instead_of_before_it() -> None:
    node = _Node(max_distances=(1.0, 1.0), lookahead_cost=16777216.0)
    assert _Search.cost(node) == 16777216.0
