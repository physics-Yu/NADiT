"""Freeze the actual source content as well as the repository commit."""

import hashlib
import subprocess
from typing import Any

from .suite import MODULE


def code_version(*, allow_dirty: bool) -> dict[str, Any]:
    """Return commit/dirty/source hash; reject uncommitted work unless authorized."""
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=MODULE, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=MODULE, text=True
    )
    dirty = bool(status.strip())
    if dirty and not allow_dirty:
        raise ValueError(
            "Experiment requires a clean checkout; "
            "--allow-dirty records exact source content."
        )
    digest = hashlib.sha256()
    files = [
        *sorted((MODULE / "src").rglob("*.py")),
        MODULE / "pyproject.toml",
        MODULE / "uv.lock",
    ]
    for path in files:
        digest.update(path.relative_to(MODULE).as_posix().encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return {"commit": commit, "dirty": dirty, "source_hash": digest.hexdigest()}


def check_version(expected: dict[str, Any]) -> None:
    """Stop dispatch/acceptance if the running experiment's code changed."""
    if code_version(allow_dirty=True) != expected:
        raise ValueError(
            "Code changed during experiment; stop and start a new version directory."
        )
