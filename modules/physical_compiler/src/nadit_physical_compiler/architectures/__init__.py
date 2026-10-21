"""Versioned, deterministic architecture generators."""

from collections.abc import Mapping
from typing import Any

from .schema import description
from .zoned import generate_zoned


def generate(family: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Expand validated family parameters into a Device document.

    Raises:
        ValueError: Unknown family or invalid parameters, with their input path.
    """
    if family != "zoned/v1":
        raise ValueError(f"architecture.family: unknown family {family!r}.")
    return generate_zoned(params)


def describe_families() -> dict[str, Any]:
    """Return supported families, parameter schemas, defaults, and constraints."""
    return {"zoned/v1": description()}
