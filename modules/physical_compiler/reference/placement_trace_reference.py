"""Observe original search queue decisions for one isolated placement stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from reference.placement_reference import SOURCES, fetch_sources
from reference.placement_reference import fixture_input as base_input
from reference.placement_reference import translation_unit as base_translation
from reference.run_reference import COMMIT, HERE


def _replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Expected exactly one source observation point: {before}")
    return source.replace(before, after)


def translation_unit(sources: dict[str, str]) -> str:
    """Add observations without changing source expressions or queue operations."""
    source = base_translation(sources)
    observer = (HERE / "placement_trace_observer.cpp").read_text(encoding="utf-8")
    source = source.replace(
        "namespace na::zoned {", observer + "\nnamespace na::zoned {", 1
    )
    source = _replace_once(
        source,
        "  observedScores.insert_or_assign(path, ObservedScore{",
        "  traceScore(node, P::getCost(node), heuristic, scale,\n"
        "      P::sumStdDeviationForGroups(scale, node.groups));\n"
        "  observedScores.insert_or_assign(path, ObservedScore{",
    )
    source = _replace_once(
        source,
        "  P placer(arch, config);",
        "  std::string method;\n"
        "  std::cin >> method >> config.maxNodes;\n"
        '  config.method = method == "astar" ? P::Config::Method::ASTAR\n'
        "      : P::Config::Method::IDS;\n"
        "  P placer(arch, config);",
    )
    replacements = {
        "      if (isGoal(*currentItem.node)) {": "\n".join(
            (
                '      traceNode("select", *currentItem.node, currentItem.priority);',
                "      if (isGoal(*currentItem.node)) {",
                '        traceNode("goal", *currentItem.node, currentItem.priority);',
            )
        ),
        "          queue.push(std::move(*minItem));": "\n".join(
            (
                '          traceNode("push", *minItem->node, minItem->priority);',
                "          queue.push(std::move(*minItem));",
            )
        ),
        "          queue.push(std::move(item));": "\n".join(
            (
                '          traceNode("push", *item.node, item.priority);',
                "          queue.push(std::move(item));",
            )
        ),
        "          queue.popAndShrink();": "\n".join(
            (
                '          traceNode("pop", *currentItem.node, currentItem.priority);',
                "          queue.popAndShrink();",
            )
        ),
        "          queue.pop();": "\n".join(
            (
                '          traceNode("pop", *currentItem.node, currentItem.priority);',
                "          queue.pop();",
            )
        ),
        "      openSet.pop();": "\n".join(
            (
                "      openSet.pop();",
                '      traceNode("pop", *itm.node, itm.priority);',
            )
        ),
        "      if (isGoal(*itm.node)) {": "\n".join(
            (
                "      if (isGoal(*itm.node)) {",
                '        traceNode("goal", *itm.node, itm.priority);',
            )
        ),
        "          openSet.emplace(cost + heuristic, neighbor);": "\n".join(
            (
                '          traceNode("push", *neighbor, cost + heuristic);',
                "          openSet.emplace(cost + heuristic, neighbor);",
            )
        ),
        "      const auto& neighbors = getNeighbors(itm.node);": "\n".join(
            (
                '      traceNode("expand", *itm.node, itm.priority);',
                "      const auto& neighbors = getNeighbors(itm.node);",
            )
        ),
        "      for (auto& neighbor : getNeighbors(currentItem.node)) {": "\n".join(
            (
                '      traceNode("expand", *currentItem.node, currentItem.priority);',
                "      for (auto& neighbor : getNeighbors(currentItem.node)) {",
            )
        ),
    }
    for before, after in replacements.items():
        source = _replace_once(source, before, after)
    return source


def fixture_input(case: dict[str, Any]) -> str:
    """Serialize the existing stage fixture plus the source search method."""
    lines = base_input(case).splitlines()
    config = case.get("config", {})
    lines.insert(
        len(case["slms"]) + 2,
        f"{config.get('method', 'ids')} {config.get('max_nodes', 10_000_000)}",
    )
    return "\n".join(lines) + "\n"


def build_reference(cache: Path, compiler: str) -> tuple[Path, list[str], str]:
    """Compile original placement with observation-only hooks and O3."""
    sources = fetch_sources(cache)
    build = cache / "standalone-placement-trace"
    build.mkdir(parents=True, exist_ok=True)
    source = build / "placement.cpp"
    source.write_text(translation_unit(sources), encoding="utf-8", newline="\n")
    binary = build / ("placement.exe" if os.name == "nt" else "placement")
    command = [compiler]
    if Path(compiler).stem == "zig":
        command.append("c++")
        if os.name == "nt":
            command.extend(("-target", "x86_64-windows-gnu"))
    command.extend(("-std=c++20", "-O3", str(source), "-o", str(binary)))
    subprocess.run(command, check=True)
    return binary, command, hashlib.sha256(source.read_bytes()).hexdigest()


def execute_case(binary: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Return original candidates, final placement, and ordered observations."""
    supplied = fixture_input(case)
    process = subprocess.run(
        [str(binary)],
        input=supplied,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return {
        "name": case["name"],
        "input": supplied,
        "result": json.loads(process.stdout),
        "events": [json.loads(line) for line in process.stderr.splitlines()],
        "raw_cpp_stdout": process.stdout,
        "raw_cpp_stderr": process.stderr,
    }


def main() -> None:
    """Build the original observer and execute supplied isolated-stage cases."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    binary, command, digest = build_reference(args.cache_dir, args.cxx)
    data = json.loads(args.fixtures.read_text(encoding="utf-8"))
    cases = data["cases"] if "cases" in data else [data]
    records = [execute_case(binary, case) for case in cases]
    report = {
        "commit": COMMIT,
        "sources": {name: sha for name, (_, sha) in SOURCES.items()},
        "translation_unit_sha256": digest,
        "compiler_command": command,
        "records": records,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Executed {len(records)} isolated original placement traces.")


if __name__ == "__main__":
    main()
