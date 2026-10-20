"""Execute pinned upstream router and order-reversing rearrangement methods."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from reference.run_reference import (
    COMMIT,
    HERE,
    SOURCES,
    build_reference,
    fetch_sources,
    fixture_input,
    normalize_events,
    site_position,
)

ROUTER_HEADER = (
    "include/na/zoned/layout_synthesizer/router/IndependentSetRouter.hpp",
    "18c81dd6b3a082e2fa9265503e7c23a9736acf529904cc0d008f0e85f121efbb",
)


def fetch_router_sources(cache: Path) -> dict[str, str]:
    """Verify the pinned method sources and router declaration.

    Args:
        cache: Independent source cache, outside project dependencies.

    Returns:
        Verified source contents with normalized line endings.

    Raises:
        ValueError: A cached or downloaded header has a different fingerprint.
    """
    sources = fetch_sources(cache)
    name = "IndependentSetRouter.hpp"
    path = cache / name
    relative, expected = ROUTER_HEADER
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


def router_translation_unit(sources: dict[str, str]) -> str:
    """Wrap the original class and complete router method bodies.

    Args:
        sources: Verified upstream headers and implementation files.

    Returns:
        Standalone C++ source. Only access labels become public for observation;
        method bodies are unchanged. The unused JSON macro is a no-op adapter.
    """
    header = sources["IndependentSetRouter.hpp"]
    start = header.index("class IndependentSetRouter : public RouterBase {")
    end = header.index("\nNLOHMANN_JSON_SERIALIZE_ENUM(", start)
    declaration = header[start:end].replace("\nprivate:", "\npublic:")
    source = sources["IndependentSetRouter.cpp"]
    methods = source[source.index("namespace na::zoned {") :]
    return "\n".join(
        (
            source[: source.index("#include")],
            (HERE / "relaxed_adapter_prefix.cpp").read_text(encoding="utf-8"),
            "namespace na::zoned {",
            declaration,
            "}",
            methods,
            (HERE / "relaxed_adapter_suffix.cpp").read_text(encoding="utf-8"),
        )
    )


def build_router(
    cache: Path, compiler: str, sources: dict[str, str]
) -> tuple[Path, list[str], str]:
    """Compile unmodified routing methods with the coordinate adapter.

    Args:
        cache: Directory for independent generated source and build artifacts.
        compiler: C++20 compiler or Zig executable.
        sources: Verified upstream source contents.

    Returns:
        Executable path, actual compiler command, and source fingerprint.
    """
    build = cache / "standalone-relaxed"
    build.mkdir(parents=True, exist_ok=True)
    source = build / "router.cpp"
    source.write_text(router_translation_unit(sources), encoding="utf-8", newline="\n")
    binary = build / ("router.exe" if os.name == "nt" else "router")
    command = [compiler]
    if Path(compiler).stem == "zig":
        command += ["c++"]
        if os.name == "nt":
            command += ["-target", "x86_64-windows-gnu"]
    command += ["-std=c++20", "-O0", str(source), "-o", str(binary)]
    subprocess.run(command, check=True)
    return binary, command, hashlib.sha256(source.read_bytes()).hexdigest()


def router_input(case: dict[str, Any]) -> str:
    """Serialize one supplied coordinate batch and threshold for original C++."""
    lines = [f"{len(case['movements'])} {case['prefer_split']}"]
    lines.extend(" ".join(map(str, movement)) for movement in case["movements"])
    return "\n".join(lines) + "\n"


def normalize_router_result(raw: dict[str, Any], scale: int) -> dict[str, Any]:
    """Convert proxy costs to micrometres and normalize unordered append order."""
    return {
        "mover_order": raw["mover_order"],
        "pairs": [
            {
                "atoms": pair["atoms"],
                "status": pair["status"],
                "merge_cost": (
                    pair["merge_cost"] / scale
                    if pair["merge_cost"] is not None
                    else None
                ),
            }
            for pair in raw["pairs"]
        ],
        "strict_groups": [sorted(group) for group in raw["strict_groups"]],
        "relaxed_groups": [sorted(group) for group in raw["relaxed_groups"]],
    }


def execute_router(binary: Path) -> list[dict[str, Any]]:
    """Execute every router fixture through the compiled upstream entry point."""
    fixture = json.loads((HERE / "relaxed_fixtures.json").read_text(encoding="utf-8"))
    records = []
    for case in fixture["cases"]:
        supplied = router_input(case)
        result = subprocess.run(
            [str(binary)], input=supplied, text=True, capture_output=True, check=True
        )
        records.append(
            {
                "name": case["name"],
                "input": supplied,
                "raw_cpp_stdout": result.stdout,
                "result": normalize_router_result(
                    json.loads(result.stdout), fixture["nm_per_um"]
                ),
            }
        )
    return records


def execute_staging(binary: Path) -> list[dict[str, Any]]:
    """Execute reversing batches using the existing original codegen adapter."""
    path = HERE / "relaxed_staging_fixtures.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for case in fixture["cases"]:
        supplied = fixture_input(fixture["slms"], case)
        result = subprocess.run(
            [str(binary)], input=supplied, text=True, capture_output=True, check=True
        )
        initial = {
            atom: site_position(fixture["slms"], site)
            for atom, site in enumerate(case["source_sites"] + case["stationary_sites"])
        }
        target = {
            atom: site_position(fixture["slms"], site)
            for atom, site in enumerate(case["target_sites"] + case["stationary_sites"])
        }
        events, final = normalize_events(
            json.loads(result.stdout), initial, fixture["nm_per_um"]
        )
        if final != target:
            raise ValueError(f"{case['name']}: upstream final endpoints differ")
        records.append(
            {
                "name": case["name"],
                "input": supplied,
                "raw_cpp_stdout": result.stdout,
                "events": events,
            }
        )
    return records


def write_json(path: Path, value: object) -> None:
    """Write a readable artifact, creating only its explicitly named parent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """Build adapters and optionally execute the router and staging fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-output", type=Path)
    parser.add_argument("--staging-expected-output", type=Path)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    sources = fetch_router_sources(args.cache_dir)
    router, router_command, router_hash = build_router(
        args.cache_dir, args.cxx, sources
    )
    codegen, codegen_command = build_reference(args.cache_dir, args.cxx, sources)
    codegen_hash = hashlib.sha256(
        (args.cache_dir / "standalone/reference.cpp").read_bytes()
    ).hexdigest()
    if args.build_only:
        print("Built original router and codegen adapters; fixtures not executed.")
        return
    routing = execute_router(router)
    staging = execute_staging(codegen)
    source_hashes = {name: digest for name, (_, digest) in SOURCES.items()}
    source_hashes["IndependentSetRouter.hpp"] = ROUTER_HEADER[1]
    write_json(
        args.output,
        {
            "source_commit": COMMIT,
            "source_sha256": source_hashes,
            "router_translation_unit_sha256": router_hash,
            "codegen_translation_unit_sha256": codegen_hash,
            "router_compiler_command": router_command,
            "codegen_compiler_command": codegen_command,
            "routing": routing,
            "staging": staging,
        },
    )
    if args.expected_output:
        write_json(
            args.expected_output,
            {
                "source_commit": COMMIT,
                "source_sha256": source_hashes,
                "translation_unit_sha256": router_hash,
                "cases": {record["name"]: record["result"] for record in routing},
            },
        )
    if args.staging_expected_output:
        write_json(
            args.staging_expected_output,
            {
                "source_commit": COMMIT,
                "source_sha256": source_hashes,
                "translation_unit_sha256": codegen_hash,
                "cases": {record["name"]: record["events"] for record in staging},
            },
        )
    print(f"Executed {len(routing)} router and {len(staging)} staging fixtures.")
    print(args.output)


if __name__ == "__main__":
    main()
