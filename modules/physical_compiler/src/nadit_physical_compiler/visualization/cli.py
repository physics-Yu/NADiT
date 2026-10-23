"""Saved-result file orchestration shared by nadit view and experiment exports."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..serialization import RESULT_SCHEMA_V2
from .export import write_html
from .result import build_result_trace


def configure(parser: argparse.ArgumentParser) -> None:
    """Register saved results, HTML output, Device override and optional trace JSON."""
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument(
        "--device", type=Path, help="Device JSON override for every result."
    )
    parser.add_argument(
        "--json", type=Path, dest="json_output", help="Optional trace JSON sidecar."
    )


def load_saved_trace(path: Path, device_override: Path | None = None) -> dict[str, Any]:
    """Read a result and its Device without reading QASM or invoking a compiler.

    Version-one Device paths resolve relative to the result file. Version two
    uses its embedded snapshot unless overridden. File/codec errors propagate.
    """
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    device = None
    if device_override is not None:
        device = json.loads(device_override.read_text(encoding="utf-8-sig"))
    elif document.get("schema") != RESULT_SCHEMA_V2:
        device = document.get("job", {}).get("device")
        if isinstance(device, str):
            device = json.loads((path.parent / device).read_text(encoding="utf-8-sig"))
    return build_result_trace(document, device, title=path.stem)


def view_results(
    results: Sequence[Path],
    output: Path,
    *,
    device_override: Path | None = None,
    json_output: Path | None = None,
) -> dict[str, Any]:
    """Export one standalone HTML from saved results, with optional trace JSON.

    Args:
        results: One or more saved result files, in display order.
        output: HTML destination; parents are created as needed.
        device_override: Optional Device JSON applied to every input.
        json_output: Optional sidecar path; no sidecar is written by default.
    Returns:
        Absolute output paths and per-trace counts for JSON CLI stdout.
    Raises:
        ValueError: Empty inputs, colliding input/output paths or invalid data.
        OSError: Input/output filesystem errors. Codec errors propagate.
    """
    if not results:
        raise ValueError("view requires at least one result file")
    inputs = {path.resolve() for path in results}
    if device_override is not None:
        inputs.add(device_override.resolve())
    destinations = [output.resolve()]
    if json_output is not None:
        destinations.append(json_output.resolve())
    if len(set(destinations)) != len(destinations) or inputs.intersection(destinations):
        raise ValueError(
            "view output paths must be distinct from inputs and each other"
        )
    traces = [load_saved_trace(path, device_override) for path in results]
    html_path = write_html(traces, output)
    if json_output is not None:
        json_output = json_output.resolve()
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(
                {"schema_version": 1, "traces": traces},
                indent=2,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "html": str(html_path),
        "json": str(json_output) if json_output else None,
        "traces": [
            {
                "title": t["title"],
                "atoms": len(t["initial"]["atoms"]),
                "steps": len(t["steps"]),
                "duration_us": t["duration_us"],
                "issues": len(t["issues"]),
            }
            for t in traces
        ],
    }
