"""Viewer noise overlays bind to the same saved execution and remain optional."""

from copy import deepcopy
from typing import Any

import pytest
from test_cost import cost_document  # noqa: F401

from nadit_physical_compiler.cost import evaluate
from nadit_physical_compiler.visualization import build_result_trace


def test_viewer_selects_latest_bound_noise_report(
    cost_document: dict[str, Any],  # noqa: F811
) -> None:
    doc = deepcopy(cost_document)
    first = evaluate(doc, model="noise/v1")
    second = evaluate(doc, model="noise/v1", params={"transport": {"move_loss": 0.01}})
    doc["cost"] = [first, second]
    trace = build_result_trace(doc)
    assert trace["noise_report"] == second
    doc["cost"][-1]["noise"]["loss"]["expected_lost_atoms"] = 99
    assert trace["noise_report"] != doc["cost"][-1]
    assert "noise_report" not in build_result_trace(cost_document)


def test_viewer_rejects_noise_for_another_execution(
    cost_document: dict[str, Any],  # noqa: F811
) -> None:
    doc = deepcopy(cost_document)
    report = evaluate(doc, model="noise/v1")
    report["result_hash"] = "different"
    doc["cost"] = [report]
    with pytest.raises(ValueError, match="Noise report"):
        build_result_trace(doc)
