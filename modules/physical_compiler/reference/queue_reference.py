"""Execute the pinned source's bounded priority queue on small operation streams."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from reference.placement_reference import SOURCES, fetch_sources
from reference.run_reference import COMMIT, HERE


def translation_unit(header: str) -> str:
    """Extract the unchanged queue template and attach a data-only I/O adapter."""
    start = header.index(
        "template <class T, class Compare = std::less<T>> class BoundedPriorityQueue"
    )
    end = header.index(
        "\n  /**\n   * @brief Performs an iterative diving search", start
    )
    return (
        "#include <algorithm>\n#include <cassert>\n#include <functional>\n"
        "#include <iostream>\n#include <memory>\n#include <vector>\n"
        "struct Source { public:\n"
        + header[start:end]
        + "\n};\n"
        + r"""
struct Item { double priority; size_t id; };
struct Compare {
  bool operator()(const Item& a, const Item& b) const {
    return a.priority < b.priority;
  }
};
int main() {
  size_t capacity, count;
  std::cin >> capacity >> count;
  Source::BoundedPriorityQueue<Item, Compare> queue(capacity);
  bool first = true;
  std::cout << "{\"popped\":[";
  const auto pop = [&](bool shrink) {
    const auto item = queue.top();
    if (!first) std::cout << ',';
    first = false;
    std::cout << '[' << item.priority << ',' << item.id << ']';
    if (shrink) queue.popAndShrink(); else queue.pop();
  };
  while (count--) {
    char kind;
    std::cin >> kind;
    if (kind == 'p') {
      Item item{};
      std::cin >> item.priority >> item.id;
      queue.push(std::move(item));
    } else if (kind == 'o') {
      bool shrink;
      std::cin >> shrink;
      pop(shrink);
    } else {
      while (!queue.empty()) pop(false);
    }
  }
  std::cout << "],\"empty\":" << (queue.empty() ? "true" : "false") << "}\n";
}
"""
    )


def execute_case(binary: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Pass operations to original C++ and return its observed removal order."""
    lines = [f"{case['capacity']} {len(case['operations'])}"]
    lines.extend(" ".join(map(str, operation)) for operation in case["operations"])
    result = subprocess.run(
        [str(binary)],
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    observed: dict[str, Any] = json.loads(result.stdout)
    return observed


def main() -> None:
    """Build the original queue and record results with pinned-source hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    header = fetch_sources(args.cache_dir)["HeuristicPlacer.hpp"]
    build = args.cache_dir / "standalone-queue"
    build.mkdir(parents=True, exist_ok=True)
    source = build / "queue.cpp"
    source.write_text(translation_unit(header), encoding="utf-8", newline="\n")
    binary = build / ("queue.exe" if os.name == "nt" else "queue")
    command = [args.cxx]
    if Path(args.cxx).stem == "zig":
        command.append("c++")
        if os.name == "nt":
            command.extend(("-target", "x86_64-windows-gnu"))
    command.extend(("-std=c++20", "-O3", str(source), "-o", str(binary)))
    subprocess.run(command, check=True)
    cases = json.loads((HERE / "queue_fixtures.json").read_text(encoding="utf-8"))
    report = {
        "commit": COMMIT,
        "source_sha256": SOURCES["HeuristicPlacer.hpp"][1],
        "translation_unit_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "compiler_command": command,
        "cases": {case["name"]: execute_case(binary, case) for case in cases},
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Executed {len(cases)} original queue cases.")


if __name__ == "__main__":
    main()
