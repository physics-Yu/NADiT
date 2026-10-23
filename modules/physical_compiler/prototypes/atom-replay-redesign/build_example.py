"""Build the prototype viewer from saved nadit.result/2 files.

Usage: python build_example.py OUTPUT.html RESULT.json[=Title] [RESULT.json[=Title] ...]
Requires nadit_physical_compiler on PYTHONPATH; reuses build_result_trace unchanged.
Asset order matches visualization.export.write_html. A noise/v1 report in the
result's "cost" list enables the loss panels.
"""

import json
import re
import sys
from html import escape
from pathlib import Path

from nadit_physical_compiler.visualization import build_result_trace

WEB = Path(__file__).resolve().parent / "web"


def main(output: str, specs: list[str]) -> None:
    """Write a standalone prototype viewer for the supplied saved traces."""
    traces = []
    for spec in specs:
        path, _, title = spec.partition("=")
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        traces.append(
            build_result_trace(
                document,
                title=title or Path(path).stem,
                description=document["job"].get("description", ""),
            )
        )
    data = json.dumps(
        {"schema_version": 1, "traces": traces},
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("<", "\\u003c")
    parts = {
        "TITLE": escape("NADiT atom replay"),
        "STYLE": (WEB / "viewer.css").read_text(encoding="utf-8"),
        "MODEL": "\n".join(
            (WEB / n).read_text(encoding="utf-8")
            for n in (
                "paths.js",
                "model.js",
                "loss.js",
                "analysis.js",
                "loss-panel.js",
                "dashboard.js",
            )
        ),
        "SCRIPT": (WEB / "viewer.js").read_text(encoding="utf-8"),
        "DATA": data,
    }
    html = re.sub(
        r"\{\{(TITLE|STYLE|MODEL|SCRIPT|DATA)\}\}",
        lambda m: parts[m[1]],
        (WEB / "viewer.html").read_text(encoding="utf-8"),
    )
    Path(output).write_text(html, encoding="utf-8", newline="\n")
    print(output, len(html), "bytes,", len(traces), "traces")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
