"""View saved compilation results or compile three Atom IR examples.

Run from the module directory with ``uv run python -m examples.visualize_compilation``.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from examples.viewer_cases import CompiledExample, compile_examples
from nadit_physical_compiler.visualization import build_result_trace
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
    """Read results or compile samples, then write offline HTML and trace JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--result",
        type=Path,
        action="append",
        help="Saved nadit.result/1 file; repeat to compare several cases.",
    )
    parser.add_argument(
        "--device",
        type=Path,
        help=(
            "Device JSON override for saved results; "
            "defaults to each result's job.device."
        ),
    )
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
    if args.device is not None and not args.result:
        parser.error("--device requires --result")
    traces = (
        [_saved_trace(path, args.device) for path in args.result]
        if args.result
        else [example_trace(example) for example in compile_examples()]
    )
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


def _saved_trace(path: Path, device_override: Path | None) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    device = document["job"]["device"]
    if device_override is not None:
        device = json.loads(device_override.read_text(encoding="utf-8-sig"))
    elif isinstance(device, str):
        device = json.loads((path.parent / device).read_text(encoding="utf-8-sig"))
    return build_result_trace(document, device, title=path.stem)


if __name__ == "__main__":
    main()
