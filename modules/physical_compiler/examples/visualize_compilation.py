"""Compile three demo jobs and view their saved results through nadit view.

Run from the module directory with ``uv run python -m examples.visualize_compilation``.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from examples.viewer_cases import CompiledExample, compile_examples
from nadit_physical_compiler.cli import main as nadit_main
from nadit_physical_compiler.visualization import build_result_trace


def example_trace(example: CompiledExample) -> dict[str, Any]:
    """Export an actual compilation with its input circuit and configuration."""
    trace = build_result_trace(
        example.document,
        title=example.title,
        description=example.description,
    )
    trace["source_qasm"] = example.source_qasm
    return trace


def main(argv: Sequence[str] | None = None) -> None:
    """Compile demo jobs, save portable results, and delegate viewing to nadit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/atom-replay/index.html")
    )
    parser.add_argument("--json", type=Path, dest="json_output")
    args = parser.parse_args(argv)
    results_dir = args.output.resolve().parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for example in compile_examples():
        path = results_dir / f"{example.title}.json"
        path.write_text(json.dumps(example.document, indent=2) + "\n", encoding="utf-8")
        paths.append(str(path))
    status = nadit_main(
        [
            "view",
            *paths,
            "-o",
            str(args.output),
            "--json",
            str(args.json_output or args.output.with_name("traces.json")),
        ]
    )
    if status:
        raise SystemExit(status)


if __name__ == "__main__":
    main()
