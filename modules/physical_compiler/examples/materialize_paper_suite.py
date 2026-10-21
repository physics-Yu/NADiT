"""Restore pinned QASM bytes from the existing archive."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

MODULE = Path(__file__).resolve().parents[1]


def materialize(archive: Path, manifest: Path) -> int:
    """Restore only manifest-named inputs whose archive and byte hashes match."""
    document = json.loads(manifest.read_text(encoding="utf-8"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != document["source"]["archive_sha256"]:
        raise ValueError("Paper archive hash does not match the fixed suite.")
    root = manifest.parent.resolve()
    with ZipFile(archive) as compressed:
        for circuit in document["circuits"]:
            target = (root / circuit["file"]).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Suite output must remain inside its directory.")
            data = compressed.read("inputs/" + circuit["file"])
            if hashlib.sha256(data).hexdigest() != circuit["sha256"]:
                raise ValueError(f"Archived circuit changed: {circuit['id']}")
            if target.exists() and target.read_bytes() != data:
                raise ValueError(f"Existing suite file differs: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return len(document["circuits"])


def main() -> None:
    """Materialize the checked paper26 input set from its local archive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=MODULE / "artifacts/search-smarter-2026-10-21.zip",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "materialized": materialize(
                    args.archive, MODULE / "suites/paper26/suite.json"
                )
            }
        )
    )


if __name__ == "__main__":
    main()
