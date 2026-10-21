"""Compile three real Atom IR examples and write an offline replay viewer.

Run from the module directory with ``uv run python -m examples.visualize_compilation``.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from examples.viewer_cases import CompiledExample, compile_examples
from nadit_physical_compiler.visualization.export import write_html
from nadit_physical_compiler.visualization.trace import build_trace


def example_trace(example: CompiledExample) -> dict[str, Any]:
    """Export an actual compilation with its input circuit and configuration."""
    trace = build_trace(
        example.result.program,
        example.entry,
        example.device,
        title=example.title,
        description=example.description,
        measurements=example.result.measurements,
        operations=example.result.operations,
    )
    trace["source_qasm"] = example.source_qasm
    trace["compilation"] = example.config
    return trace


def main(argv: Sequence[str] | None = None) -> None:
    """Compile samples and write HTML plus JSON to the requested local paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/atom-replay/index.html")
    )
    parser.add_argument(
        "--json",
        type=Path,
        dest="json_output",
        help="Trace JSON path; defaults to traces.json beside the HTML file.",
    )
    args = parser.parse_args(argv)
    traces = [example_trace(example) for example in compile_examples()]
    html_path = write_html(traces, args.output)
    json_path = (args.json_output or html_path.with_name("traces.json")).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {"schema_version": 1, "traces": traces},
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "html": str(html_path),
                "json": str(json_path),
                "traces": [
                    {
                        "title": trace["title"],
                        "atoms": len(trace["initial"]["atoms"]),
                        "steps": len(trace["steps"]),
                        "duration_us": trace["duration_us"],
                        "issues": len(trace["issues"]),
                    }
                    for trace in traces
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
