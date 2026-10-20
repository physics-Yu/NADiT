"""Execute pinned original placement, geometry, candidate, and scoring methods."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from reference.run_reference import COMMIT, HERE

SOURCES = {
    "Architecture.hpp": (
        "include/na/zoned/Architecture.hpp",
        "aaad72585f543a4620d696d95ef4a27efddb141453d54c85784a819dd12df2cf",
    ),
    "Architecture.cpp": (
        "src/na/zoned/Architecture.cpp",
        "2f1861cfb807d72e5a96927c36e746845f2453c6069581034b30a7d808d78725",
    ),
    "HeuristicPlacer.hpp": (
        "include/na/zoned/layout_synthesizer/placer/HeuristicPlacer.hpp",
        "e5c528ebb5009fbc08621a9bd5e2e021baff007c9e1d46e81065beff4c6c13ec",
    ),
    "HeuristicPlacer.cpp": (
        "src/na/zoned/layout_synthesizer/placer/HeuristicPlacer.cpp",
        "0864e0905f39b1604dac544447166f6cccc5eace85f2cd93ebeb665912be8e5c",
    ),
}


def fetch_sources(cache: Path) -> dict[str, str]:
    """Fetch missing sources and verify exact pinned bytes before extraction."""
    cache.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, (relative, expected) in SOURCES.items():
        path = cache / name
        if not path.exists():
            url = (
                "https://raw.githubusercontent.com/munich-quantum-toolkit/qmap/"
                f"{COMMIT}/{relative}"
            )
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ValueError(f"{name}: expected {expected}, received {actual}")
        result[name] = data.decode("utf-8").replace("\r\n", "\n")
    return result


def translation_unit(sources: dict[str, str]) -> str:
    """Extract complete algorithm bodies and expose private observation points.

    The two heuristic overloads are renamed at their definitions only. Adapter
    wrappers retain their original names, call the original bodies, and record
    jobs and scores. Placement, candidate, window, and search bodies are intact.
    """
    arch_header = sources["Architecture.hpp"]
    slm = arch_header[
        arch_header.index("namespace na::zoned {") : arch_header.index(
            "/// Class to define zone architecture."
        )
    ]
    arch_decl = """
struct Architecture {
  std::vector<std::unique_ptr<SLM>> storageZones;
  std::vector<std::unique_ptr<std::array<SLM, 2>>> entanglementZones;
  SLMMap<std::vector<std::vector<Site>>> entanglementToNearestStorageSite;
  SLMMap<std::vector<std::vector<SLMMap<std::vector<std::vector<Site>>>>>>
      storageToNearestEntanglementSite;
  bool isValidSLMPosition(const SLM&, size_t, size_t) const;
  std::pair<size_t,size_t> exactSLMLocation(const SLM&,size_t,size_t) const;
  const SLM& findNearestStorageSLM(size_t,size_t) const;
  const SLM& findNearestEntanglementSLM(size_t,size_t,size_t,size_t) const;
  void preprocessing();
  double distance(const SLM&,size_t,size_t,const SLM&,size_t,size_t) const;
  const Site& nearestStorageSite(const SLM&,size_t,size_t) const;
  const Site& nearestEntanglementSite(
      const SLM&,size_t,size_t,const SLM&,size_t,size_t) const;
  double nearestEntanglementSiteDistance(
      const SLM&,size_t,size_t,const SLM&,size_t,size_t) const;
  Site otherEntanglementSite(const SLM&,size_t,size_t) const;
};
using Placement = std::vector<Site>;
using QubitPair = std::array<qc::Qubit, 2>;
using TwoQubitGateLayer = std::vector<QubitPair>;
using SiteSet = std::unordered_set<Site>;
class PlacerBase {};
"""
    header = sources["HeuristicPlacer.hpp"]
    declaration = header[
        header.index("template <class T>\nusing RowColumnMap") : header.index(
            "NLOHMANN_JSON_SERIALIZE_ENUM("
        )
    ]
    declaration = (
        declaration.replace(
            "class HeuristicPlacer : public PlacerBase {",
            "class HeuristicPlacer : public PlacerBase {\npublic:",
        )
        .replace("private:", "public:")
        .replace(" override;", ";")
    )
    heuristic_declarations = re.findall(
        r"\[\[nodiscard\]\] static auto\s+getHeuristic\([\s\S]*?-> float;",
        declaration,
    )
    if len(heuristic_declarations) != 2:
        raise ValueError("Expected exactly two upstream heuristic declarations")
    insertion = "\n".join(
        item.replace("getHeuristic(", "originalHeuristic(")
        for item in heuristic_declarations
    )
    last = declaration.rfind("};")
    declaration = declaration[:last] + insertion + "\n" + declaration[last:]
    arch_source = sources["Architecture.cpp"]
    equality = arch_source[
        arch_source.index("auto SLM::operator==") : arch_source.index(
            "auto Architecture::fromJSON"
        )
    ]
    geometry = arch_source[arch_source.index("auto Architecture::isValidSLMPosition") :]
    source = sources["HeuristicPlacer.cpp"]
    methods = source[source.index("namespace na::zoned {") :].replace(
        "auto HeuristicPlacer::getHeuristic(",
        "auto HeuristicPlacer::originalHeuristic(",
    )
    return "\n".join(
        (
            source[: source.index("#include")],
            (HERE / "placement_adapter_prefix.cpp").read_text(encoding="utf-8"),
            slm,
            arch_decl,
            declaration,
            equality,
            geometry,
            methods,
            (HERE / "placement_adapter_suffix.cpp").read_text(encoding="utf-8"),
        )
    )


def build_reference(
    cache: Path, compiler: str, sources: dict[str, str]
) -> tuple[Path, list[str], str]:
    """Compile the source-derived standalone placement executable."""
    build = cache / "standalone-placement"
    build.mkdir(parents=True, exist_ok=True)
    path = build / "placement.cpp"
    path.write_text(translation_unit(sources), encoding="utf-8", newline="\n")
    binary = build / ("placement.exe" if os.name == "nt" else "placement")
    command = [compiler]
    if Path(compiler).stem == "zig":
        command.append("c++")
        if os.name == "nt":
            command.extend(("-target", "x86_64-windows-gnu"))
    command.extend(("-std=c++20", "-O0", str(path), "-o", str(binary)))
    subprocess.run(command, check=True)
    return binary, command, hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_input(case: dict[str, Any]) -> str:
    """Serialize fixture data; no placement algorithm runs in this adapter."""
    slms = case["slms"]
    lines = [str(len(slms))]
    for slm in slms:
        fields = (
            slm["id"],
            slm.get("pair_group", -1),
            slm["rows"],
            slm["columns"],
            *slm["pitch"],
            *slm["origin"],
        )
        lines.append(" ".join(map(str, fields)))
    cfg = case.get("config", {})
    lines.append(
        " ".join(
            map(
                str,
                (
                    int(cfg.get("use_window", True)),
                    cfg.get("window_min_width", 16),
                    cfg.get("window_ratio", 1),
                    cfg.get("window_share", 0.8),
                    cfg.get("deepening_factor", 0.01),
                    cfg.get("deepening_value", 0),
                    cfg.get("lookahead_factor", 0.4),
                    cfg.get("reuse_level", 5),
                    cfg.get("trials", 1),
                    cfg.get("queue_capacity", 30),
                ),
            )
        )
    )
    lines.append(str(case.get("initial_count", 0)))
    for key in ("nearest_storage_queries", "nearest_entanglement_queries"):
        values = case.get(key, [])
        lines.append(str(len(values)))
        lines.extend(" ".join(map(str, query)) for query in values)
    lines.append(str(case.get("stage", "none")))
    for key in ("placement", "gates", "next_gates"):
        values = case.get(key, [])
        lines.append(str(len(values)))
        lines.extend(" ".join(map(str, row)) for row in values)
    for key in ("previous_reuse", "next_reuse"):
        values = case.get(key, [])
        lines.append(" ".join(map(str, (len(values), *values))))
    return "\n".join(lines) + "\n"


def main() -> None:
    """Build and execute C++; record raw evidence and portable oracle results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-oracle", type=Path)
    args = parser.parse_args()
    sources = fetch_sources(args.cache_dir)
    binary, command, digest = build_reference(args.cache_dir, args.cxx, sources)
    cases = json.loads((HERE / "placement_fixtures.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    records = []
    for case in cases:
        supplied = fixture_input(case)
        result = subprocess.run(
            [str(binary)],
            input=supplied,
            text=True,
            capture_output=True,
            check=True,
            timeout=60,
        )
        records.append(
            {
                "name": case["name"],
                "input": supplied,
                "raw_cpp_stdout": result.stdout,
                "result": json.loads(result.stdout),
            }
        )
    report = {
        "commit": COMMIT,
        "sources": {name: digest for name, (_, digest) in SOURCES.items()},
        "translation_unit_sha256": digest,
        "compiler_command": command,
        "records": records,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.write_oracle:
        oracle = {
            "commit": COMMIT,
            "sources": report["sources"],
            "translation_unit_sha256": digest,
            "units": "integer micrometres",
            "cases": {item["name"]: item["result"] for item in records},
        }
        args.write_oracle.write_text(
            json.dumps(oracle, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Executed {len(records)} original C++ placement cases.")
    print(args.output)


if __name__ == "__main__":
    main()
