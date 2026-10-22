"""Parameter-path assignment, zipped axes, and Cartesian expansion."""

import re
from collections.abc import Iterator, Mapping
from copy import deepcopy
from itertools import product
from typing import Any


def tokens(path: str) -> tuple[str | int, ...]:
    """Parse dotted object names and nonnegative array indices."""
    if not re.fullmatch(r"[A-Za-z_][\w-]*(?:\[\d+\]|\.[A-Za-z_][\w-]*)*", path):
        raise ValueError(f"Invalid parameter path {path!r}.")
    return tuple(
        int(index) if index else name
        for name, index in re.findall(r"([A-Za-z_][\w-]*)|\[(\d+)\]", path)
    )


def get_path(value: Any, path: str) -> Any:
    """Read a nested object/array parameter."""
    for key in tokens(path):
        value = value[key]
    return value


def set_path(
    value: dict[str, Any], path: str, replacement: Any, *, field: str = "sweep"
) -> None:
    """Set one parameter, requiring its parent path to exist."""
    parts = tokens(path)
    target: Any = value
    try:
        for key in parts[:-1]:
            target = target[key]
        target[parts[-1]] = deepcopy(replacement)
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"{field}.{path}: parent path does not exist.") from error


def expand(
    base: Mapping[str, Any],
    sweep: Mapping[str, list[Any]],
    zipped: list[list[str]],
    exclude: list[dict[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Yield deterministic products; zipped dimensions advance together."""
    used: set[str] = set()
    axes: list[list[dict[str, Any]]] = []
    for group in zipped:
        if (
            not group
            or any(key not in sweep or key in used for key in group)
            or len(set(group)) != len(group)
        ):
            raise ValueError(
                "zip: paths must be distinct and belong to one sweep group."
            )
        if len({len(sweep[key]) for key in group}) != 1:
            raise ValueError("zip: all grouped axes need the same length.")
        used.update(group)
        axes.append(
            [
                dict(zip(group, values, strict=True))
                for values in zip(*(sweep[key] for key in group), strict=True)
            ]
        )
    for path, values in sweep.items():
        tokens(path)
        if not isinstance(values, list) or not values:
            raise ValueError(f"sweep.{path}: expected a nonempty array.")
        if path not in used:
            axes.append([{path: value} for value in values])
    for choices in product(*axes):
        result = deepcopy(dict(base))
        for assignment in choices:
            for path, value in assignment.items():
                set_path(result, path, value)
        if not any(
            all(get_path(result, path) == value for path, value in rule.items())
            for rule in exclude
        ):
            yield result
