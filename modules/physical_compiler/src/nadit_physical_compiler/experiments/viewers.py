"""Export selected anomaly results through the packaged saved-result viewer."""

import os
import shlex
from pathlib import Path
from typing import Any

from ..visualization.cli import view_results


def viewer_record(
    directory: Path, row: dict[str, Any], *, generate: bool
) -> dict[str, Any]:
    """Return invocation and export status without recompiling missing runs.

    Missing or malformed results are reported per run so summary generation can
    continue. The output directory is deterministic for each existing run ID.
    """
    relative = row["provenance"].get("result_path")
    output = (directory / "viewers" / row["run_id"] / "index.html").resolve()
    result = (directory / relative).resolve() if relative else None
    argv = ["nadit", "view", str(result), "-o", str(output)] if result else None
    command = None
    if result is not None:

        def quote(value: str) -> str:
            return (
                "'" + value.replace("'", "''") + "'"
                if os.name == "nt"
                else shlex.quote(value)
            )

        command = f"nadit view {quote(str(result))} -o {quote(str(output))}"
    status: dict[str, Any] = {"status": "not_requested", "path": None, "reason": None}
    if generate:
        if result is None:
            status.update(
                status="not_saved",
                reason="No retained result; use store=all or failures for future runs.",
            )
        elif not result.is_file():
            status.update(status="missing_result", reason=str(result))
        else:
            try:
                view_results([result], output)
            except (
                OSError,
                UnicodeError,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
            ) as error:
                status.update(status="error", reason=f"{type(error).__name__}: {error}")
            else:
                status.update(status="written", path=str(output))
    return {"viewer_command": command, "viewer_argv": argv, "viewer": status}
