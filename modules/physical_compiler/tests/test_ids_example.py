"""The existing study selects IDS without changing its input or result case."""

import json
import subprocess
import sys

import pytest
from test_reuse_metrics import MODULE, _audit_metrics


@pytest.mark.parametrize("bit", [0, 1])
def test_study_selects_ids_and_reports_actual_transport_metrics(bit: int) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(MODULE / "examples/compile_multipair.py"),
            "--placement",
            "ids",
            "--transport",
            "strict",
            "--reuse",
            "--bit",
            str(bit),
        ],
        cwd=MODULE,
        check=True,
        capture_output=True,
        text=True,
    )
    metrics, end = json.JSONDecoder().raw_decode(result.stdout)
    assert metrics["placement"] == "ids"
    assert metrics["placement_config"]["trials"] == 4
    assert metrics["placement_config"]["queue_capacity"] == 100
    assert [item["value"] for item in metrics["measurements"]] == [bit]
    assert len(_audit_metrics(metrics, result.stdout[end:])) == (6 if bit else 4)
