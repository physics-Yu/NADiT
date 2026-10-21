"""Offline viewer packaging preserves data without executing it as markup."""

import json
import re
from pathlib import Path

from nadit_physical_compiler.visualization.export import write_html


def test_document_embeds_trace_and_assets_without_external_requests(
    tmp_path: Path,
) -> None:
    trace = {"title": "Simple trace", "steps": [], "atom_ir": "TICK(duration_us=0)"}
    target = write_html([trace], tmp_path / "nested/viewer.html")
    html = target.read_text(encoding="utf-8")
    data = re.search(
        r'<script[^>]*id="atom-replay-data"[^>]*>(.*?)</script>', html, re.S
    )

    assert target.is_absolute()
    assert data is not None
    assert json.loads(data[1]) == {"schema_version": 1, "traces": [trace]}
    assert not re.search(r"<script[^>]*\bsrc\s*=|<link[^>]*\bhref\s*=", html)
    assert not re.search(r"\{\{(?:TITLE|STYLE|MODEL|SCRIPT|DATA)\}\}", html)


def test_script_closing_text_stays_in_json_and_template_values_are_not_recursive(
    tmp_path: Path,
) -> None:
    marker = '</script><script>throw new Error("injected")</script>'
    target = write_html(
        [{"title": marker}], tmp_path / "safe.html", title="<Inspector> {{DATA}}"
    )
    html = target.read_text(encoding="utf-8")
    data = re.search(
        r'<script[^>]*id="atom-replay-data"[^>]*>(.*?)</script>', html, re.S
    )

    assert "<title>&lt;Inspector&gt; {{DATA}}</title>" in html
    assert marker not in html
    assert data is not None
    assert json.loads(data[1])["traces"][0]["title"] == marker


def test_export_is_byte_identical_for_identical_inputs(tmp_path: Path) -> None:
    first = write_html([{"title": "Repeat"}], tmp_path / "one.html")
    second = write_html([{"title": "Repeat"}], tmp_path / "two.html")
    assert first.read_bytes() == second.read_bytes()
