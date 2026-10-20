"""Candidate windows and perimeter expansion from the pinned QMAP placer."""

# Adapted from MQT QMAP HeuristicPlacer.cpp (MIT).
# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and license.

from math import floor
from typing import Literal

from .scoring import PlacementConfig

_Bounds = tuple[int, int, int, int]


def candidate_indices(
    n_rows: int,
    n_columns: int,
    center: tuple[int, int],
    available: frozenset[tuple[int, int]],
    job_count: int,
    config: PlacementConfig,
    *,
    stage: Literal["gate", "storage"],
    initial_options: int = 0,
) -> tuple[tuple[int, int], ...]:
    """Enumerate available sites in the source window and its added perimeter.

    Args:
        n_rows: Number of rows in the selected nearest SLM.
        n_columns: Number of columns in that SLM.
        center: Nearest row and column, including an occupied nearest site.
        available: Coordinates eligible for an option in that SLM.
        job_count: Number of gates or atoms to place in the current stage.
        config: Window enablement, minimum width, aspect ratio, and share.
        stage: Gate expansion grows by one; storage expansion grows by two.
        initial_options: Existing alternatives, such as a storage stay option.

    Returns:
        Free coordinates in initial row-major and then source perimeter order.
        Enumeration ends once the share is reached or the entire SLM is covered.
    """
    minimum_height = _round(config.window_ratio * config.window_min_width)
    bounds = (
        _bounds(
            n_rows,
            n_columns,
            center,
            config.window_min_width,
            minimum_height,
        )
        if config.use_window
        else (0, n_rows, 0, n_columns)
    )
    selected = [
        (row, column)
        for row in range(bounds[0], bounds[1])
        for column in range(bounds[2], bounds[3])
        if (row, column) in available
    ]
    expansion = 0
    while (
        config.use_window
        and len(selected) + initial_options < config.window_share * job_count
        and bounds != (0, n_rows, 0, n_columns)
    ):
        expansion += 1 if stage == "gate" else 2
        if config.window_ratio < 1:
            width = config.window_min_width + expansion
            height = _round(config.window_ratio * width)
        else:
            height = minimum_height + expansion
            width = _round(height / config.window_ratio)
        expanded = _bounds(n_rows, n_columns, center, width, height)
        selected.extend(
            index for index in _perimeter(bounds, expanded) if index in available
        )
        bounds = expanded
    return tuple(selected)


def _round(value: float) -> int:
    """Use C++ std::round for the nonnegative window dimensions."""
    return floor(value + 0.5)


def _bounds(
    n_rows: int,
    n_columns: int,
    center: tuple[int, int],
    width: int,
    height: int,
) -> _Bounds:
    row, column = center
    return (
        max(row - height // 2, 0),
        min(row + height // 2 + 1, n_rows),
        max(column - width // 2, 0),
        min(column + width // 2 + 1, n_columns),
    )


def _perimeter(previous: _Bounds, expanded: _Bounds) -> tuple[tuple[int, int], ...]:
    row_low, row_high, column_low, column_high = previous
    new_row_low, new_row_high, new_column_low, new_column_high = expanded
    return (
        *(
            (row, column)
            for row in range(new_row_low, row_low)
            for column in range(new_column_low, new_column_high)
        ),
        *(
            (row, column)
            for row in range(row_high, new_row_high)
            for column in range(new_column_low, new_column_high)
        ),
        *(
            (row, column)
            for column in range(new_column_low, column_low)
            for row in range(row_low, row_high)
        ),
        *(
            (row, column)
            for column in range(column_high, new_column_high)
            for row in range(row_low, row_high)
        ),
    )
