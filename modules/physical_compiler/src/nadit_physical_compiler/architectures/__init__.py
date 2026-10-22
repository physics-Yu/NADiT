"""Versioned, deterministic architecture generators."""

from collections.abc import Mapping
from typing import Any

from .schema import description
from .shared import description as shared_description
from .shared import generate_shared
from .zoned import generate_zoned


def generate(family: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Expand validated family parameters into a Device document.

    Raises:
        ValueError: Unknown family or invalid parameters, with their input path.
    """
    generators = {"zoned/v1": generate_zoned, "shared/v1": generate_shared}
    if family not in generators:
        raise ValueError(f"architecture.family: unknown family {family!r}.")
    return generators[family](params)


def describe_families() -> dict[str, Any]:
    """Return supported families, parameter schemas, defaults, and constraints."""
    return {"zoned/v1": description(), "shared/v1": shared_description()}
