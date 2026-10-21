"""Fixed circuit suites with verified byte and manifest identities."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..job_fields import array_at, integer_at, keys_at, object_at, required, string_at
from ..provenance import canonical_hash

MODULE = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class Circuit:
    """One immutable circuit input, with declared physical width."""

    id: str
    path: Path
    sha256: str
    qubits: int


@dataclass(frozen=True, slots=True)
class Suite:
    """A checked circuit collection; gate translation is fixed by gate_set."""

    name: str
    sha256: str
    circuits: tuple[Circuit, ...]
    gate_set: tuple[str, ...]


def load_suite(value: Any, base: Path) -> Suite:
    """Resolve a suite name/path and verify every pinned QASM file.

    Raises:
        ValueError: Missing, changed, duplicated, or malformed suite inputs.
    """
    reference = {"name": value} if isinstance(value, str) else object_at(value, "suite")
    keys_at(reference, {"name", "sha256"}, "suite")
    name = string_at(required(reference, "name", "suite"), "suite.name")
    candidate = base / name
    path = candidate if candidate.is_file() else candidate / "suite.json"
    if not path.is_file():
        path = MODULE / "suites" / name / "suite.json"
    document = object_at(json.loads(path.read_text(encoding="utf-8")), "suite")
    keys_at(document, {"schema", "name", "circuits", "gate_set", "source"}, "suite")
    if document.get("schema") != "nadit.suite/1":
        raise ValueError("suite.schema: expected nadit.suite/1.")
    digest = canonical_hash(document)
    if reference.get("sha256", digest) != digest:
        raise ValueError("suite.sha256: manifest content changed.")
    circuits = []
    for index, raw in enumerate(array_at(document["circuits"], "suite.circuits")):
        item = object_at(raw, f"suite.circuits[{index}]")
        keys_at(item, {"id", "file", "sha256", "qubits"}, f"suite.circuits[{index}]")
        source = (path.parent / item["file"]).resolve()
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise ValueError(f"suite.circuits[{index}].sha256: changed input {source}.")
        width = integer_at(item["qubits"], f"suite.circuits[{index}].qubits")
        if width < 0:
            raise ValueError("suite qubits must be nonnegative.")
        circuits.append(
            Circuit(string_at(item["id"], "suite.circuit.id"), source, actual, width)
        )
    if not circuits or len({c.id for c in circuits}) != len(circuits):
        raise ValueError("suite.circuits: expected nonempty distinct circuit IDs.")
    return Suite(document["name"], digest, tuple(circuits), tuple(document["gate_set"]))
