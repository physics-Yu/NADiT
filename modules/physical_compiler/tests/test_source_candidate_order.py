"""Reference ordered-set tie behavior for gate and storage placement jobs."""

from nadit_physical_compiler.device import Position, SLMSite
from nadit_physical_compiler.placement.candidates import gate_jobs, storage_jobs
from nadit_physical_compiler.state import AtomState, SLMHolder


def _atom(atom_id: int, x: float, y: float) -> AtomState:
    return AtomState(atom_id, Position(x, y), SLMHolder(f"s{atom_id}"))


def test_equal_distance_gate_jobs_use_descending_canonical_pairs() -> None:
    pair = (
        SLMSite("left", Position(0, 10)),
        SLMSite("right", Position(2, 10)),
    )
    gates = (
        (_atom(0, 0, 0), _atom(7, 2, 0)),
        (_atom(5, 0, 0), _atom(2, 2, 0)),
        (_atom(1, 0, 0), _atom(6, 2, 0)),
    )

    jobs = gate_jobs(gates, (pair,))

    assert [
        tuple(atom_id for atom_id, _ in job.options[0].assignments) for job in jobs
    ] == [(5, 2), (1, 6), (0, 7)]


def test_gate_distance_precedes_the_pair_tie_breaker() -> None:
    pair = (
        SLMSite("left", Position(0, 10)),
        SLMSite("right", Position(2, 10)),
    )

    jobs = gate_jobs(
        (
            (_atom(8, 0, 0), _atom(9, 2, 0)),
            (_atom(0, 0, -100), _atom(1, 2, -100)),
        ),
        (pair,),
    )

    assert [job.options[0].assignments[0][0] for job in jobs] == [0, 8]


def test_equal_distance_storage_jobs_use_ascending_atom_ids() -> None:
    atoms = (_atom(4, 0, 10), _atom(8, -2, 8), _atom(1, 2, 8))

    jobs = storage_jobs(atoms, (SLMSite("storage", Position(0, 0)),))

    assert [job.options[0].assignments[0][0] for job in jobs] == [4, 1, 8]


def test_farthest_storage_atom_tie_preserves_first_encounter() -> None:
    atoms = (_atom(9, -2, 10), _atom(1, 2, 10), _atom(0, 0, 1))

    jobs = storage_jobs(atoms, (SLMSite("storage", Position(0, 0)),))

    assert [job.options[0].assignments[0][0] for job in jobs] == [9, 1, 0]
