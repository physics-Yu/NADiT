"""Canonical content identities and local software provenance."""

import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def canonical_hash(value: Mapping[str, Any]) -> str:
    """Hash sorted, compact JSON; reject nonfinite or non-JSON values.

    Args:
        value: JSON object whose content identifies an input or resolved job.
    Returns:
        Lowercase SHA-256 hexadecimal digest of canonical UTF-8 JSON.
    Raises:
        TypeError: Values cannot be represented as JSON.
        ValueError: A numeric value is not finite.
    """
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _git_state() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, None
    return commit, bool(status.strip())


def collect_provenance(
    job: Mapping[str, Any], *, inputs: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Return software versions, content-based job identity, and git state.

    Args:
        job: Resolved job document. It is not modified.
        inputs: Captured QASM and Device hashes. When provided, replace both
            input references before hashing; without them retain version-1 hashing.
    Returns:
        Provenance without timestamps; unavailable package or git data is null.
        Git dirtiness includes tracked index and worktree changes only.
    Raises:
        KeyError: Input hashes or resolved circuit fields are missing.
        TypeError: The job cannot be represented as JSON.
        ValueError: The job contains nonfinite numbers.
    """
    identity = deepcopy(dict(job))
    if inputs is not None:
        identity["circuit"]["qasm"] = inputs["qasm_sha256"]
        identity["device"] = inputs["device_sha256"]
    job_hash = canonical_hash(identity)
    git_commit, git_dirty = _git_state()
    from .compilers.registry import identity as compiler_identity

    return {
        "compiler": compiler_identity(dict(job.get("strategy", {}))),
        "package_version": _package_version("nadit-physical-compiler"),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "job_hash": job_hash,
        "python_version": platform.python_version(),
        "qiskit_version": _package_version("qiskit"),
    }
