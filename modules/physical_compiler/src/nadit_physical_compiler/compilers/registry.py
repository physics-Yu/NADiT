"""The single compiler catalogue consumed by jobs, discovery, and experiments."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ..compiler import CompilationStrategy
from ..job_fields import object_at, string_at
from ..support import UnsupportedJob
from .contracts import CompilerContext, ProgramCompiler

type Compiler = CompilationStrategy | ProgramCompiler
type Reasons = list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class CompilerRegistration:
    """A compiler's public configuration, admission, factory, and budget mapping.

    Admission returns all structured reasons, preserving existing stage paths.
    Factories return the compiler and resolved options; neither samples results.
    """

    name: str
    version: str
    options_schema: Callable[[], dict[str, Any]]
    entry_providers: tuple[str, ...]
    admit: Callable[[CompilerContext, dict[str, Any]], Reasons]
    build: Callable[[CompilerContext, dict[str, Any]], tuple[Compiler, dict[str, Any]]]
    defaults: dict[str, Any] = field(default_factory=dict)
    budgets: dict[str, str] = field(default_factory=dict)
    budget_applicable: Callable[[str, dict[str, Any]], bool] = lambda name, options: (
        True
    )
    supports_feedback: bool = True
    modes: dict[str, str] = field(default_factory=dict)


COMPILERS: dict[str, CompilerRegistration] = {}


def registration(spec: dict[str, Any]) -> CompilerRegistration:
    """Resolve an explicit name or the legacy Search Smarter default."""
    name = string_at(spec.get("kind", "search_smarter"), "strategy.kind")
    if name not in COMPILERS:
        raise ValueError(f"strategy.kind: unknown compiler {name!r}.")
    return COMPILERS[name]


def entry_for(spec: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any]:
    """Select a compiler's default provider without generating any layout."""
    registered = registration(spec)
    return (
        deepcopy(entry)
        if entry is not None
        else {"kind": registered.entry_providers[0]}
    )


def select(
    context: CompilerContext,
    spec: dict[str, Any],
    *,
    entry: dict[str, Any] | None = None,
) -> tuple[Compiler, dict[str, Any]]:
    """Collect admission failures before invoking a registered compiler factory."""
    spec = object_at(spec, "strategy")
    registered = registration(spec)
    options = {
        **deepcopy(registered.defaults),
        **deepcopy({k: v for k, v in spec.items() if k != "kind"}),
    }
    reasons = registered.admit(context, options)
    provider = entry_for(spec, entry)
    if provider.get("kind") not in registered.entry_providers:
        reasons.append(
            {
                "stage": "entry",
                "kind": registered.name,
                "reason": (
                    f"supports entry providers {registered.entry_providers}, "
                    f"got {provider.get('kind')!r}"
                ),
            }
        )
    if not registered.supports_feedback and any(
        n.condition is not None for n in context.dag.nodes
    ):
        reasons.append(
            {
                "stage": "strategy",
                "kind": registered.name,
                "reason": "does not support measurement feedback",
            }
        )
    if reasons:
        raise UnsupportedJob(reasons)
    compiler, resolved = registered.build(context, options)
    return compiler, {"kind": registered.name, **resolved}


def identity(spec: dict[str, Any]) -> dict[str, str]:
    """Return the emission-version identity of a registered compiler."""
    item = registration(spec)
    return {"kind": item.name, "version": item.version}


def describe_compilers() -> dict[str, Any]:
    """Generate public compiler descriptions from exactly the registered entries."""
    return {
        name: {
            "version": item.version,
            "options": item.options_schema(),
            "defaults": deepcopy(item.defaults),
            "entry_providers": list(item.entry_providers),
            "budgets": dict(item.budgets),
            "supports_feedback": item.supports_feedback,
            "supports": (item.admit.__doc__ or "").strip(),
            "modes": dict(item.modes),
        }
        for name, item in COMPILERS.items()
    }


# Built-ins are loaded after the catalogue contracts exist.
from .builtin import register_builtins  # noqa: E402

register_builtins(COMPILERS)
