"""Package replay traces and viewer assets into one offline HTML document."""

import json
import re
from collections.abc import Sequence
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Any


def write_html(
    traces: Sequence[dict[str, Any]],
    path: str | Path,
    *,
    title: str = "NADiT Atom Replay",
) -> Path:
    """Write a self-contained viewer for previously exported traces.

    Args:
        traces: Version-one traces returned by build_trace.
        path: Destination HTML file; parent directories are created as needed.
        title: Browser document title.

    Returns:
        Absolute path to the written document.
    """
    assets = files("nadit_physical_compiler.visualization").joinpath("web")
    data = json.dumps(
        {"schema_version": 1, "traces": list(traces)},
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("<", "\\u003c")
    replacements = {
        "TITLE": escape(title),
        "STYLE": assets.joinpath("viewer.css").read_text(encoding="utf-8"),
        "MODEL": "\n".join(
            assets.joinpath(name).read_text(encoding="utf-8")
            for name in ("paths.js", "model.js", "analysis.js", "dashboard.js")
        ),
        "SCRIPT": assets.joinpath("viewer.js").read_text(encoding="utf-8"),
        "DATA": data,
    }
    template = assets.joinpath("viewer.html").read_text(encoding="utf-8")
    document = re.sub(
        r"\{\{(TITLE|STYLE|MODEL|SCRIPT|DATA)\}\}",
        lambda match: replacements[match[1]],
        template,
    )
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8", newline="\n")
    return destination
