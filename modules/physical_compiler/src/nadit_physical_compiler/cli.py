"""Compile declarative jobs and inspect versioned results from the command line."""

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from .compiler import CompilationResult
from .device import load_device, loads_device
from .job import compile_job, load_job
from .serialization import (
    RESULT_SCHEMA_V2,
    decode_dag,
    decode_layout,
    decode_result,
    encode_result,
)
from .validation import ValidationReport, validate_result


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(f"{self.prog}: {message}")


class _CompilationError(Exception):
    """Separate compiler failures from input and output document errors."""


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="nadit", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_command = commands.add_parser("compile", help="Compile a saved JSON job.")
    compile_command.add_argument("job", type=Path)
    compile_command.add_argument("-o", "--output", type=Path)
    compile_command.add_argument("--validate", action="store_true")
    compile_command.add_argument("--text", type=Path)
    validate_command = commands.add_parser("validate", help="Validate a saved result.")
    validate_command.add_argument("result", type=Path)
    validate_command.add_argument(
        "--device", type=Path, help="Explicit Device override; recorded in the report."
    )
    compare_command = commands.add_parser("compare", help="Compare two saved results.")
    compare_command.add_argument("left", type=Path)
    compare_command.add_argument("right", type=Path)
    return parser


def _json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=True, indent=2, allow_nan=False) + "\n"


def _validation_status(report: ValidationReport) -> int:
    if report["ok"]:
        return 0
    failure = report["failure"]
    assert failure is not None
    print(
        f"nadit: validation failed: {failure['rule']}: {failure['message']}",
        file=sys.stderr,
    )
    return 3


def _compile(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    job = load_job(args.job)
    try:
        result = compile_job(job)
    except Exception as error:
        raise _CompilationError(f"{type(error).__name__}: {error}") from error
    validation = (
        validate_result(
            result, entry_state=job.entry_state, device=job.device, dag=job.dag
        )
        if args.validate
        else None
    )
    document = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        validation=validation,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
    )
    if args.output is not None:
        args.output.write_text(_json(document), encoding="utf-8", newline="\n")
    if args.text is not None:
        args.text.write_text(result.program.to_text(), encoding="utf-8", newline="\n")
    return document, _validation_status(validation) if validation is not None else 0


def _read_result(path: Path) -> tuple[dict[str, Any], CompilationResult]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict):
            raise ValueError("expected a JSON object")
        result = decode_result(document)
        decode_layout(document["entry_state"])
        for field in ("job", "provenance", "metrics"):
            if not isinstance(document[field], dict):
                raise ValueError(f"{field}: expected a JSON object")
        return document, result
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError(f"result {path}: {error}") from error


def _validate(
    path: Path, device_override: Path | None = None
) -> tuple[dict[str, Any], int]:
    document, result = _read_result(path)
    version_two = document["schema"] == RESULT_SCHEMA_V2
    device_document = (
        str(device_override.resolve())
        if device_override is not None
        else document["device"]
        if version_two
        else document["job"]["device"]
    )
    if isinstance(device_document, str):
        device = load_device(path.parent / device_document)
    elif isinstance(device_document, dict):
        device = loads_device(json.dumps(device_document))
    else:
        raise ValueError("job.device: expected a path or Device JSON object")
    report = validate_result(
        result,
        entry_state=decode_layout(document["entry_state"]),
        device=device,
        dag=decode_dag(document["dag"]) if version_two else None,
        detailed=True,
    )
    report_document: dict[str, Any] = dict(report)
    if device_override is not None:
        report_document["device_override"] = str(device_override.resolve())
    return report_document, _validation_status(report)


def _difference(left: Any, right: Any, path: str) -> dict[str, Any] | None:
    if left == right:
        return None
    if isinstance(left, dict) and isinstance(right, dict):
        for key in dict.fromkeys((*left, *right)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                return {
                    "path": child_path,
                    "left": left.get(key),
                    "right": right.get(key),
                }
            if found := _difference(left[key], right[key], child_path):
                return found
    elif isinstance(left, list) and isinstance(right, list):
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            if found := _difference(a, b, f"{path}[{index}]"):
                return found
        index = min(len(left), len(right))
        return {
            "path": f"{path}[{index}]",
            "left": left[index] if index < len(left) else None,
            "right": right[index] if index < len(right) else None,
        }
    return {"path": path, "left": left, "right": right}


def _first_difference(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any] | None:
    for field in (
        "layers",
        "program",
        "operations",
        "measurements",
        "entry_state",
        "exit_state",
        "dag",
        "device",
        "inputs",
    ):
        found = _difference(left.get(field), right.get(field), field)
        if found is None:
            continue
        found["kind"] = "layer" if field == "layers" else field
        match = re.match(
            r"program\.steps\[(\d+)\](?:\.actions\[(\d+)\])?", found["path"]
        )
        if match:
            found["step"] = int(match[1])
            found["kind"] = "step"
            if match[2] is not None:
                found["action"] = int(match[2])
                found["kind"] = "action"
        return found
    return None


def _compare(left_path: Path, right_path: Path) -> dict[str, Any]:
    left, _ = _read_result(left_path)
    right, _ = _read_result(right_path)
    metrics: dict[str, dict[str, Any]] = {}
    for key in sorted(left["metrics"].keys() | right["metrics"].keys()):
        a, b = left["metrics"].get(key), right["metrics"].get(key)
        if a == b:
            continue
        metrics[key] = {"left": a, "right": b}
        if isinstance(a, int | float) and isinstance(b, int | float):
            metrics[key]["delta"] = b - a
    difference = _first_difference(left, right)
    return {
        "equal": not metrics and difference is None,
        "metrics": metrics,
        "first_difference": difference,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run compile, validate, or compare, writing JSON to stdout.

    Args:
        argv: Command arguments, or process arguments when omitted.

    Returns:
        Zero on success, one for invalid input or output paths, two for compiler
        errors, and three for a failed validation report. Diagnostics use stderr.
    """
    try:
        args = _parser().parse_args(argv)
        if args.command == "compile":
            document, status = _compile(args)
        elif args.command == "validate":
            document, status = _validate(args.result, args.device)
        else:
            document, status = _compare(args.left, args.right), 0
        print(_json(document), end="")
        return status
    except _CompilationError as error:
        print(f"nadit: compilation failed: {error}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
        print(f"nadit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
