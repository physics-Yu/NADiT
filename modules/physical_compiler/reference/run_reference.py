"""Compile and execute pinned, unmodified QMAP rearrangement method bodies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

COMMIT = "cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d"
SOURCES = {
    "CodeGenerator.cpp": (
        "src/na/zoned/code_generator/CodeGenerator.cpp",
        "3223883b05b32aa696fc2ca07e6ad45e7c90396cbd9d83a2443618267d62620a",
    ),
    "CodeGenerator.hpp": (
        "include/na/zoned/code_generator/CodeGenerator.hpp",
        "25041fa74fd25d29160a8db979639d6592065cebbeb42da891c069d18f34ccf9",
    ),
    "IndependentSetRouter.cpp": (
        "src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp",
        "17a6273c5b77ada9fc1248a54df183d39055c925f4e01b5f967edebfec6e7bdf",
    ),
}
HERE = Path(__file__).resolve().parent


def fetch_sources(cache: Path) -> dict[str, str]:
    """Download missing pinned sources and reject different cached bytes."""
    cache.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, (relative, expected) in SOURCES.items():
        path = cache / name
        if not path.exists():
            url = f"https://raw.githubusercontent.com/munich-quantum-toolkit/qmap/{COMMIT}/{relative}"
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ValueError(f"{name}: expected SHA-256 {expected}, received {actual}")
        sources[name] = data.decode("utf-8").replace("\r\n", "\n")
    return sources


def declaration(header: str) -> str:
    """Extract the complete original nested class declaration."""
    start = header.index("  class RearrangementGenerator {")
    brace = header.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (header[end] == "{") - (header[end] == "}")
        end += 1
    if header[end] != ";":
        raise ValueError("Unexpected end of upstream class declaration")
    return header[start : end + 1]


def translation_unit(sources: dict[str, str]) -> str:
    """Wrap original declarations/helpers/methods with data-only C++ adapters."""
    source = sources["CodeGenerator.cpp"]
    helper = source[
        source.index("// Unique-y comparison") : source.index(
            "auto CodeGenerator::appendSingleQubitGates("
        )
    ]
    enumerator = source[
        source.index("namespace {\n[[nodiscard]] auto enumerate") : source.index(
            "auto CodeGenerator::appendRearrangement("
        )
    ]
    methods = source[
        source.index(
            "auto CodeGenerator::RearrangementGenerator::getLocationFromSite("
        ) : source.index("auto CodeGenerator::generate(")
    ]
    return "\n".join(
        (
            source[: source.index("#include")],
            (HERE / "adapter_prefix.cpp").read_text(encoding="utf-8"),
            "class CodeGenerator {\npublic:\n",
            declaration(sources["CodeGenerator.hpp"]),
            "\n};\n",
            helper,
            enumerator,
            methods,
            (HERE / "adapter_suffix.cpp").read_text(encoding="utf-8"),
        )
    )


def build_reference(
    cache: Path, compiler: str, sources: dict[str, str]
) -> tuple[Path, list[str]]:
    """Compile the extracted upstream C++ with the selected compiler."""
    build = cache / "standalone"
    build.mkdir(parents=True, exist_ok=True)
    source = build / "reference.cpp"
    source.write_text(translation_unit(sources), encoding="utf-8", newline="\n")
    binary = build / ("reference.exe" if os.name == "nt" else "reference")
    command = [compiler]
    if Path(compiler).stem == "zig":
        command += ["c++"]
        if os.name == "nt":
            command += ["-target", "x86_64-windows-gnu"]
    command += ["-std=c++20", "-O0", str(source), "-o", str(binary)]
    subprocess.run(command, check=True)
    return binary, command


def site_position(slms: list[dict[str, Any]], site: list[int]) -> tuple[int, int]:
    """Resolve a fixture site to the same integer coordinate convention."""
    slm, row, column = site
    descriptor = slms[slm]
    return (
        descriptor["origin"][0] + column * descriptor["pitch"][0],
        descriptor["origin"][1] + row * descriptor["pitch"][1],
    )


def fixture_input(slms: list[dict[str, Any]], case: dict[str, Any]) -> str:
    """Serialize geometry and supplied moves to the adapter's numeric input."""
    lines = [
        " ".join(map(str, (*slm["origin"], *slm["pitch"], slm["rows"], slm["columns"])))
        for slm in slms
    ]
    source = case["source_sites"] + case["stationary_sites"]
    target = case["target_sites"] + case["stationary_sites"]
    if len(source) != len(target):
        raise ValueError(f"Fixture {case['name']} has unequal endpoint counts")
    lines.append(str(len(source)))
    lines.extend(
        " ".join(map(str, (*before, *after)))
        for before, after in zip(source, target, strict=True)
    )
    count = len(case["source_sites"])
    lines.extend((str(count), " ".join(map(str, range(count)))))
    return "\n".join(lines) + "\n"


def normalize_events(
    raw: list[dict[str, Any]],
    initial: dict[int, tuple[int, int]],
    scale: int,
) -> tuple[list[dict[str, Any]], dict[int, tuple[int, int]]]:
    """Enrich original atom-ID events with their tracked positions."""
    current = dict(initial)
    carried: set[int] = set()
    normalized = []
    for event in raw:
        atoms, kind = event["atoms"], event["kind"]
        if kind == "load":
            if carried.intersection(atoms):
                raise ValueError("Upstream loaded an already carried atom")
            carried.update(atoms)
        elif kind == "move":
            if not set(atoms) <= carried:
                raise ValueError("Upstream moved an atom before loading it")
            current.update(
                (atom, (position[0], position[1]))
                for atom, position in zip(atoms, event["targets_nm"], strict=True)
            )
        elif kind == "store":
            if not set(atoms) <= carried:
                raise ValueError("Upstream stored an atom before loading it")
            carried.difference_update(atoms)
        else:
            raise ValueError(f"Unknown upstream operation {kind!r}")
        normalized.append(
            {
                "kind": kind,
                "positions": [
                    [atom, current[atom][0] / scale, current[atom][1] / scale]
                    for atom in sorted(atoms)
                ],
            }
        )
    if carried:
        raise ValueError("Upstream batch left atoms loaded")
    return normalized, current


def compare_events(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> None:
    """Compare each operation and atom endpoint without relaxing event order."""
    if len(expected) != len(actual):
        raise ValueError(
            f"Event count: reference={len(expected)}, candidate={len(actual)}"
        )
    for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
        if left["kind"] != right["kind"]:
            raise ValueError(f"Event {index}: operation kinds differ")
        left_positions = sorted(left["positions"])
        right_positions = sorted(right["positions"])
        if len(left_positions) != len(right_positions):
            raise ValueError(f"Event {index}: affected atom counts differ")
        for a, b in zip(left_positions, right_positions, strict=True):
            if a[0] != b[0] or not all(
                math.isclose(x, y, abs_tol=1e-9, rel_tol=0)
                for x, y in zip(a[1:], b[1:], strict=True)
            ):
                raise ValueError(f"Event {index}: reference atom {a} != candidate {b}")


def main() -> None:
    """Build once, execute each upstream fixture, and optionally compare traces."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-json", type=Path)
    parser.add_argument("--expected-output", type=Path)
    args = parser.parse_args()
    sources = fetch_sources(args.cache_dir)
    binary, command = build_reference(args.cache_dir, args.cxx, sources)
    fixture = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    candidate = (
        json.loads(args.candidate_json.read_text(encoding="utf-8"))
        if args.candidate_json
        else None
    )
    records = []
    for case in fixture["cases"]:
        supplied_input = fixture_input(fixture["slms"], case)
        result = subprocess.run(
            [str(binary)],
            input=supplied_input,
            text=True,
            capture_output=True,
            check=True,
        )
        raw = json.loads(result.stdout)
        source = case["source_sites"] + case["stationary_sites"]
        target = case["target_sites"] + case["stationary_sites"]
        initial = {
            atom: site_position(fixture["slms"], site)
            for atom, site in enumerate(source)
        }
        expected_final = {
            atom: site_position(fixture["slms"], site)
            for atom, site in enumerate(target)
        }
        events, final = normalize_events(raw, initial, fixture["nm_per_um"])
        if final != expected_final:
            raise ValueError(f"{case['name']}: upstream final endpoints differ")
        if candidate is not None:
            compare_events(events, candidate[case["name"]])
        records.append(
            {
                "name": case["name"],
                "axis_policy": case["axis_policy"],
                "input": supplied_input,
                "raw_cpp_stdout": result.stdout,
                "events": events,
                "final_positions_nm": final,
                "candidate_equal": True if candidate is not None else None,
            }
        )
    report = {
        "source_commit": COMMIT,
        "scope": "original C++ RearrangementGenerator only",
        "source_sha256": {name: digest for name, (_, digest) in SOURCES.items()},
        "translation_unit_sha256": hashlib.sha256(
            (args.cache_dir / "standalone/reference.cpp").read_bytes()
        ).hexdigest(),
        "compiler_command": command,
        "fixtures": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.expected_output is not None:
        expected = {
            "source_commit": COMMIT,
            "source_sha256": report["source_sha256"],
            "translation_unit_sha256": report["translation_unit_sha256"],
            "cases": {record["name"]: record["events"] for record in records},
        }
        args.expected_output.parent.mkdir(parents=True, exist_ok=True)
        args.expected_output.write_text(
            json.dumps(expected, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Executed {len(records)} pinned upstream C++ fixtures.")
    if candidate is not None:
        print(f"All {len(records)} candidate event traces match.")
    print(args.output)


if __name__ == "__main__":
    main()
