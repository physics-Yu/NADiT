"""Execute all pinned C++ pipeline stages on fixed native static circuits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from reference.placement_reference import SOURCES as PLACEMENT_SOURCES
from reference.relaxed_reference import ROUTER_HEADER
from reference.run_reference import COMMIT, HERE
from reference.run_reference import SOURCES as ROUTING_SOURCES

SOURCES = {
    **PLACEMENT_SOURCES,
    **ROUTING_SOURCES,
    "IndependentSetRouter.hpp": ROUTER_HEADER,
    "ASAPScheduler.cpp": (
        "src/na/zoned/scheduler/ASAPScheduler.cpp",
        "31009474fbdbca13b4f55ab4ec9d5c738e4a4b7558af974e7736119a32a894f4",
    ),
    "ASAPScheduler.hpp": (
        "include/na/zoned/scheduler/ASAPScheduler.hpp",
        "e22d674f9da212e16671bda23672734e27355e418bee274ec856ca56a3a50fa6",
    ),
    "VertexMatchingReuseAnalyzer.cpp": (
        "src/na/zoned/reuse_analyzer/VertexMatchingReuseAnalyzer.cpp",
        "76f2d2866ae57c23821bbf3f496824a53f35398b5c14daf176b0de7905886f67",
    ),
    "VertexMatchingReuseAnalyzer.hpp": (
        "include/na/zoned/reuse_analyzer/VertexMatchingReuseAnalyzer.hpp",
        "dff4d7c5a3aa5d8dcafcd0d8e11aa34aa80a7ee5e3fdbad32c4c98507c665706",
    ),
    "Types.hpp": (
        "include/na/zoned/Types.hpp",
        "a0b581c8695afb650dcc62d3c0d2da67eec0326ff6fa24c1c354300f55bc5201",
    ),
    "Compiler.hpp": (
        "include/na/zoned/Compiler.hpp",
        "b444bee64fb60aa8de13117e75d4b53d60025810bbdbab8c6327b38e76823db2",
    ),
}


def fetch_sources(cache: Path) -> dict[str, str]:
    """Download missing pinned files and verify bytes before extraction."""
    cache.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, (relative, expected) in SOURCES.items():
        path = cache / name
        if not path.exists():
            url = f"https://raw.githubusercontent.com/munich-quantum-toolkit/qmap/{COMMIT}/{relative}"
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise ValueError(f"{name}: expected {expected}, received {digest}")
        result[name] = data.decode("utf-8").replace("\r\n", "\n")
    return result


def translation_unit(sources: dict[str, str]) -> str:
    """Wrap complete original stage bodies with data and output adapters."""
    architecture_header = sources["Architecture.hpp"]
    slm = architecture_header[
        architecture_header.index("namespace na::zoned {") : architecture_header.index(
            "/// Class to define zone architecture."
        )
    ]
    architecture = """
struct Architecture {
  std::vector<std::unique_ptr<SLM>> storageZones;
  std::vector<std::unique_ptr<std::array<SLM, 2>>> entanglementZones;
  std::vector<size_t> rydbergRangeMinX, rydbergRangeMinY;
  std::vector<size_t> rydbergRangeMaxX, rydbergRangeMaxY;
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
class SchedulerBase {};
class ReuseAnalyzerBase {};
class PlacerBase {};
class RouterBase {};
"""
    parts = [
        (HERE / "placement_adapter_prefix.cpp").read_text(encoding="utf-8"),
        (HERE / "pipeline_adapter_prefix.cpp").read_text(encoding="utf-8"),
        slm,
        architecture,
        "}",
        sources["Types.hpp"][sources["Types.hpp"].index("namespace na::zoned {") :],
    ]
    for name in (
        "ASAPScheduler",
        "VertexMatchingReuseAnalyzer",
        "HeuristicPlacer",
        "IndependentSetRouter",
        "CodeGenerator",
    ):
        header = sources[f"{name}.hpp"]
        start = header.index("namespace na::zoned {")
        end = header.find("NLOHMANN_JSON_SERIALIZE_ENUM(", start)
        declaration = header[start : end if end >= 0 else None]
        declaration = declaration.replace(" override", "")
        if end >= 0:
            declaration += "\n} // namespace na::zoned\n"
        parts.append(declaration)
    arch_source = sources["Architecture.cpp"]
    parts.extend(
        (
            "namespace na::zoned {",
            arch_source[
                arch_source.index("auto SLM::operator==") : arch_source.index(
                    "auto Architecture::fromJSON"
                )
            ],
            arch_source[arch_source.index("auto Architecture::isValidSLMPosition") :],
        )
    )
    for name in (
        "ASAPScheduler",
        "VertexMatchingReuseAnalyzer",
        "HeuristicPlacer",
        "IndependentSetRouter",
        "CodeGenerator",
    ):
        source = sources[f"{name}.cpp"]
        parts.append(source[source.index("namespace na::zoned {") :])
    parts.append((HERE / "pipeline_adapter_suffix.cpp").read_text(encoding="utf-8"))
    return "\n".join(parts)


def build_reference(
    cache: Path,
    compiler: str,
    sources: dict[str, str],
    *,
    optimization: int = 0,
) -> tuple[Path, list[str], str]:
    """Compile all source stages into one standalone C++20 executable."""
    build = cache / "standalone-pipeline"
    build.mkdir(parents=True, exist_ok=True)
    source = build / "pipeline.cpp"
    source.write_text(translation_unit(sources), encoding="utf-8", newline="\n")
    name = "pipeline" if optimization == 0 else f"pipeline-o{optimization}"
    binary = build / (f"{name}.exe" if os.name == "nt" else name)
    command = [compiler]
    if Path(compiler).stem == "zig":
        command.append("c++")
        if os.name == "nt":
            command.extend(("-target", "x86_64-windows-gnu"))
    command.extend(("-std=c++20", f"-O{optimization}", str(source), "-o", str(binary)))
    subprocess.run(command, check=True)
    return binary, command, hashlib.sha256(source.read_bytes()).hexdigest()


def fixture_input(case: dict[str, Any]) -> str:
    """Serialize circuit, SLM geometry, and configuration without decisions."""
    lines = [str(len(case["slms"]))]
    for slm in case["slms"]:
        fields = (
            slm["id"],
            slm.get("pair_group", -1),
            slm["rows"],
            slm["columns"],
            *slm["pitch"],
            *slm["origin"],
        )
        lines.append(" ".join(map(str, fields)))
    ranges = case.get("rydberg_range", [])
    lines.append(str(len(ranges)))
    lines.extend(" ".join(map(str, (*lower, *upper))) for lower, upper in ranges)
    config = case.get("config", {})
    fields = (
        config.get("max_filling_factor", 0.9),
        config.get("method", "ids"),
        config.get("routing", "relaxed"),
        int(config.get("use_window", True)),
        config.get("window_min_width", 16),
        config.get("window_ratio", 1),
        config.get("window_share", 0.8),
        config.get("deepening_factor", 0.01),
        config.get("deepening_value", 0),
        config.get("lookahead_factor", 0.4),
        config.get("reuse_level", 5),
        config.get("trials", 4),
        config.get("queue_capacity", 100),
        config.get("prefer_split", 1),
        config.get("max_nodes", 10_000_000),
    )
    lines.append(" ".join(map(str, fields)))
    lines.append(f"{case['n_qubits']} {len(case['operations'])}")
    for operation in case["operations"]:
        targets = operation["targets"]
        parameters = operation.get("parameters", [])
        lines.append(
            " ".join(
                map(
                    str,
                    (
                        operation["type"],
                        len(targets),
                        *targets,
                        len(parameters),
                        *parameters,
                    ),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _completed_stages(stderr: str) -> dict[str, Any]:
    """Read observation records emitted only after a source stage returns."""
    result = {}
    for line in stderr.splitlines():
        if line.startswith("TRACE "):
            record = json.loads(line.removeprefix("TRACE "))
            result[record["stage"]] = record["result"]
    return result


def _failure_details(stderr: str) -> dict[str, Any]:
    """Identify the first source stage that did not complete."""
    completed = _completed_stages(stderr)
    order = ("scheduling", "reuse", "placement", "routing", "codegen")
    return {
        "failed_stage": next(
            (stage for stage in order if stage not in completed), None
        ),
        "completed_stages": completed,
    }


def execute_case(binary: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Execute one input and preserve success, source failure, or timeout."""
    supplied = fixture_input(case)
    try:
        result = subprocess.run(
            [str(binary)],
            input=supplied,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
        return {
            "name": case["name"],
            "input": supplied,
            "status": "timeout",
            "raw_cpp_stdout": stdout or "",
            "raw_cpp_stderr": stderr or "",
            **_failure_details(stderr or ""),
        }
    record: dict[str, Any] = {
        "name": case["name"],
        "input": supplied,
        "status": "ok" if result.returncode == 0 else "source_failure",
        "returncode": result.returncode,
        "raw_cpp_stdout": result.stdout,
        "raw_cpp_stderr": result.stderr,
    }
    if result.returncode == 0:
        record["result"] = json.loads(result.stdout)
    else:
        record.update(_failure_details(result.stderr))
    return record


def main() -> None:
    """Build once, execute fixed inputs, and save raw and portable evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-oracle", type=Path)
    parser.add_argument("--optimization", type=int, choices=(0, 3), default=0)
    args = parser.parse_args()
    sources = fetch_sources(args.cache_dir)
    binary, command, digest = build_reference(
        args.cache_dir, args.cxx, sources, optimization=args.optimization
    )
    cases = json.loads((HERE / "pipeline_fixtures.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    records = [execute_case(binary, case) for case in cases]
    report = {
        "commit": COMMIT,
        "scope": "original stages chained in Compiler order; data-only IR adapters",
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
            "cases": {
                item["name"]: {
                    "status": item["status"],
                    **(
                        {
                            "failed_stage": item["failed_stage"],
                            "completed_stages": item["completed_stages"],
                        }
                        if "failed_stage" in item
                        else {}
                    ),
                    **(
                        {
                            "result": {
                                key: value
                                for key, value in item["result"].items()
                                if key != "timings"
                            }
                        }
                        if "result" in item
                        else {}
                    ),
                }
                for item in records
            },
        }
        args.write_oracle.write_text(
            json.dumps(oracle, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Executed {len(records)} original C++ pipeline cases.")
    for item in records:
        print(f"{item['name']}: {item['status']}")
    print(args.output)


if __name__ == "__main__":
    main()
