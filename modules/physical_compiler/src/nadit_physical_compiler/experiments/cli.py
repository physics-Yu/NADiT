"""Small experiment command dispatch, separate from compilation CLI plumbing."""

import argparse
import json
from pathlib import Path
from typing import Any

from .costs import reevaluate
from .runner import run_experiment, status
from .spec import load_spec, plan
from .suite import MODULE
from .summary import summarize
from .version import code_version


def configure(parser: argparse.ArgumentParser) -> None:
    """Register planning, running, status, summary, and reevaluation commands."""
    commands = parser.add_subparsers(dest="experiment_command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("spec", type=Path)
        command.add_argument("--allow-dirty", action="store_true")
        if name == "run":
            command.add_argument("--output", type=Path)
            command.add_argument("--workers", type=int, default=1)
            command.add_argument("--new-version", action="store_true")
    commands.add_parser("status").add_argument("directory", type=Path)
    summary = commands.add_parser("summarize")
    summary.add_argument("directory", type=Path)
    summary.add_argument("--cost-id")
    summary.add_argument(
        "--viewers",
        action="store_true",
        help="Export saved anomaly results as offline HTML.",
    )
    summary.add_argument(
        "--objectives", nargs="+", default=["duration_us", "quality_loss", "slm_sites"]
    )
    cost = commands.add_parser("cost")
    cost.add_argument("directory", type=Path)
    cost.add_argument("--model", default="additive")
    cost.add_argument("--params", type=Path, required=True)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    """Run an experiment operation and return JSON-ready results."""
    if args.experiment_command == "status":
        return status(args.directory.resolve())
    if args.experiment_command == "summarize":
        return summarize(
            args.directory.resolve(),
            cost_id=args.cost_id,
            objectives=tuple(args.objectives),
            viewers=args.viewers,
        )
    if args.experiment_command == "cost":
        params = json.loads(args.params.read_text(encoding="utf-8-sig"))
        return reevaluate(
            args.directory.resolve(), ({"model": args.model, "params": params},)
        )
    spec = load_spec(args.spec)
    if args.experiment_command == "plan":
        return plan(spec, code_version(allow_dirty=args.allow_dirty))
    directory = args.output or MODULE / "artifacts" / "experiments" / spec.name
    return run_experiment(
        spec,
        directory,
        workers=args.workers,
        allow_dirty=args.allow_dirty,
        new_version=args.new_version,
    )
