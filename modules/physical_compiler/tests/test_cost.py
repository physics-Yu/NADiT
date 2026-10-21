"""Analytic activity/cost cases and immutable validated-result reevaluation."""

import json
from copy import deepcopy
from math import exp, log1p
from pathlib import Path
from typing import Any

import pytest

from nadit_physical_compiler.activity import derive_activity
from nadit_physical_compiler.atom_ir import (
    AtomProgram,
    AtomStep,
    Load,
    Move,
    Rotation,
    Store,
    Wait,
)
from nadit_physical_compiler.cli import main
from nadit_physical_compiler.cost import UnvalidatedResult, evaluate, prepare
from nadit_physical_compiler.cost.additive import AdditiveModel
from nadit_physical_compiler.device import (
    AOD,
    Device,
    Position,
    Region,
    SLMSite,
    Timing,
    Zone,
)
from nadit_physical_compiler.job import compile_job, load_job
from nadit_physical_compiler.replay import replay
from nadit_physical_compiler.serialization import (
    encode_device,
    encode_layout,
    encode_program,
    encode_result,
)
from nadit_physical_compiler.state import (
    AODGrid,
    AODLine,
    AtomLayout,
    AtomState,
    SLMHolder,
)
from nadit_physical_compiler.validation import validate_result

MODULE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cost_document() -> dict[str, Any]:
    job = load_job(MODULE / "examples/jobs/multipair.json")
    result = compile_job(job)
    report = validate_result(
        result, entry_state=job.entry_state, device=job.device, dag=job.dag
    )
    assert report["ok"]
    return encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        device=job.device,
        dag=job.dag,
        inputs=job.inputs,
        validation=report,
    )


def timeline_document() -> dict[str, Any]:
    """Hand-built activity fixture: short LOAD, MOVE, short STORE, feedback tail."""
    device = Device(
        "activity",
        (
            Zone("s", Region(0, 0, 5, 0), (), "storage"),
            Zone("g", Region(3, 4, 3, 4), (), "entanglement"),
        ),
        (
            SLMSite("s0", Position(0, 0)),
            SLMSite("s1", Position(5, 0)),
            SLMSite("t", Position(3, 4)),
        ),
        (AOD("a", Region(-1, -1, 10, 10), 1),),
        Timing(2, 1, 2),
    )
    entry = AtomLayout(
        (
            AtomState(7, Position(0, 0), SLMHolder("s0")),
            AtomState(9, Position(5, 0), SLMHolder("s1")),
        ),
        qubit_to_atom=(7, 9),
    )
    grid = AODGrid("a", (AODLine(0, 0),), (AODLine(0, 0),))
    target = AODGrid("a", (AODLine(0, 4),), (AODLine(0, 3),))
    program = AtomProgram(
        (
            AtomStep(
                (
                    Load(grid, ((7, 0, 0),), duration_us=2),
                    Rotation("r", (9,), "X", 0.3, duration_us=5),
                )
            ),
            AtomStep((Move(target, ((7, Position(3, 4)),), duration_us=10),)),
            AtomStep(
                (Store(AODGrid("a"), ((7, "t"),), duration_us=1), Wait(duration_us=4))
            ),
        )
    )
    return {
        "schema": "nadit.result/2",
        "device": encode_device(device),
        "entry_state": encode_layout(entry),
        "exit_state": encode_layout(replay(program, entry, device)),
        "program": encode_program(program),
        "dag": {"nodes": []},
        "operations": [],
        "measurements": [{"available_at_us": 24}],
        "validation": {
            "ok": True,
            "semantic": "checked",
            "checks": ["pulse", "timing"],
        },
    }


def test_activity_counts_short_parallel_tail_holder_changes_and_wait() -> None:
    activity = derive_activity(timeline_document())
    a, b = activity.per_atom
    assert activity.duration_us == 24
    assert (a.busy_us, a.idle_us, b.busy_us, b.idle_us) == (13, 11, 5, 19)
    assert a.idle_by_holder_role == {"aod:storage": 3, "slm:entanglement": 8}
    assert a.loads == a.stores == a.move_count == 1
    assert a.transfers == 2 and a.move_distance_um == 5
    assert b.rotation_count == 1
    assert all(atom.busy_us + atom.idle_us == 24 for atom in activity.per_atom)
    assert [event.kind for event in activity.events][-1] == "Wait"


def test_additive_matches_hand_calculated_log_contributions() -> None:
    document = timeline_document()
    activity = derive_activity(document)
    params = {
        "single_infidelity": 0.01,
        "transfer_loss": 0.02,
        "slm_t2_us": 100,
        "aod_t2_us": 10,
        "heating_per_um": 0.1,
        "heating_us_per_um2": 0.2,
    }
    report = AdditiveModel().evaluate(activity, document, params)
    terms = report["metrics"]["contributions"]
    assert terms["single"] == pytest.approx(log1p(-0.01))
    assert terms["transfer"] == pytest.approx(2 * log1p(-0.02))
    assert terms["idle"] == pytest.approx(-27 / 100 - 3 / 10)
    assert terms["heating"] == pytest.approx(-0.1 * 5 - 0.2 * 25 / 10)
    assert report["metrics"]["log_fidelity"] == pytest.approx(sum(terms.values()))
    assert report["metrics"]["fidelity"] == pytest.approx(exp(sum(terms.values())))


def test_adjacent_waits_do_not_expand_idle_records_per_step() -> None:
    document = timeline_document()
    document["program"] = encode_program(
        AtomProgram(tuple(AtomStep((Wait(duration_us=1),)) for _ in range(100)))
    )
    document["measurements"] = []
    activity = derive_activity(document)
    assert len(activity.events) == 100
    assert all(
        len(atom.idle) == 1 and atom.idle_us == 100 and atom.busy_us == 0
        for atom in activity.per_atom
    )


def test_activity_from_real_result_covers_every_atom_and_gate(
    cost_document: dict[str, Any],
) -> None:
    activity = prepare(cost_document)
    assert all(
        atom.busy_us + atom.idle_us == pytest.approx(activity.duration_us)
        for atom in activity.per_atom
    )
    assert (
        sum(atom.loads for atom in activity.per_atom)
        == cost_document["metrics"]["atom_load_count"]
    )
    assert sum(atom.cz_count for atom in activity.per_atom) == 2 * sum(
        cost_document["metrics"]["cz_gates_per_pulse"]
    )
    assert all(
        event.coverage is not None
        for event in activity.events
        if event.kind == "CZPulse"
    )


def test_cost_reevaluation_is_deterministic_and_does_not_compile(
    cost_document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = deepcopy(cost_document)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Cost invoked compilation")

    monkeypatch.setattr("nadit_physical_compiler.job.compile_job", fail)
    ideal = evaluate(cost_document)
    noisy = evaluate(cost_document, params={"cz_infidelity": 0.01})
    assert noisy == evaluate(cost_document, params={"cz_infidelity": 0.01})
    assert ideal["metrics"]["fidelity"] == 1
    assert noisy["metrics"]["fidelity"] < 1
    assert noisy["params_hash"] != ideal["params_hash"]
    assert noisy["result_hash"] == ideal["result_hash"]
    assert cost_document == original


@pytest.mark.parametrize("change", ["missing", "false", "checks", "legacy", "tampered"])
def test_cost_rejects_unvalidated_or_changed_results(
    cost_document: dict[str, Any], change: str
) -> None:
    document = deepcopy(cost_document)
    if change == "missing":
        document.pop("validation")
    elif change == "false":
        document["validation"]["ok"] = False
    elif change == "checks":
        document["validation"]["checks"] = ["replay"]
    elif change == "legacy":
        document["schema"] = "nadit.result/1"
    else:
        document["exit_state"]["atoms"][0]["position"]["x"] += 1
    with pytest.raises(UnvalidatedResult):
        evaluate(document)


@pytest.mark.parametrize(
    "params",
    [
        {"transfer_loss": 1},
        {"cz_infidelity": -0.1},
        {"slm_t2_us": 0},
        {"heating_per_um": -1},
        {"typo": 1},
    ],
)
def test_invalid_noise_parameters_are_rejected(params: dict[str, Any]) -> None:
    document = timeline_document()
    with pytest.raises(ValueError):
        AdditiveModel().evaluate(derive_activity(document), document, params)


def test_cost_cli_appends_without_changing_execution(
    cost_document: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps(cost_document))
    assert main(["cost", str(path), "--into", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    saved = json.loads(path.read_text())
    assert saved.pop("cost") == [report]
    assert saved == cost_document
    assert main(["cost", str(path), "--into", str(path)]) == 0
    capsys.readouterr()
    assert len(json.loads(path.read_text())["cost"]) == 1
