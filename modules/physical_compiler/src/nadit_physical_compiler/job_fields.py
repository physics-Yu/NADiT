"""Small, path-aware readers for the declarative job JSON boundary."""

from math import isfinite
from typing import Any, cast


def object_at(value: Any, path: str) -> dict[str, Any]:
    """Read a JSON object, reporting its input path on a type error."""
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object.")
    return value


def keys_at(value: dict[str, Any], allowed: set[str], path: str) -> None:
    """Reject unknown object fields without ignoring misspelled options."""
    for key in value:
        if key not in allowed:
            raise ValueError(f"{path}.{key}: unknown field.")


def required(value: dict[str, Any], key: str, path: str) -> Any:
    """Read a required field with a useful missing-field path."""
    if key not in value:
        raise ValueError(f"{path}.{key}: required field.")
    return value[key]


def array_at(value: Any, path: str) -> list[Any]:
    """Read a JSON array."""
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected an array.")
    return value


def string_at(value: Any, path: str) -> str:
    """Read a string."""
    if not isinstance(value, str):
        raise ValueError(f"{path}: expected a string.")
    return value


def integer_at(value: Any, path: str) -> int:
    """Read an integer, excluding JSON booleans."""
    if type(value) is not int:
        raise ValueError(f"{path}: expected an integer.")
    return value


def number_at(value: Any, path: str) -> float:
    """Read a finite number, excluding JSON booleans."""
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{path}: expected a finite number.")
    return cast(float, value)


def boolean_at(value: Any, path: str) -> bool:
    """Read a JSON boolean."""
    if type(value) is not bool:
        raise ValueError(f"{path}: expected a boolean.")
    return value
