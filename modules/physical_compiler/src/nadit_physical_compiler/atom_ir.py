"""Immutable atom actions with explicit timing and Stim-style text output."""

import json
from dataclasses import dataclass
from math import isfinite
from typing import Literal, assert_never

from .device import Position, Region
from .physical_ir import ResultRef
from .state import AODGrid


@dataclass(frozen=True, slots=True, kw_only=True)
class _TimedAction:
    """Timing and source provenance shared by all atom actions."""

    duration_us: float
    source_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isfinite(self.duration_us) or self.duration_us < 0:
            raise ValueError("Action duration_us must be finite and nonnegative.")


@dataclass(frozen=True, slots=True)
class Load(_TimedAction):
    """Transfer atoms from SLM traps into AOD intersections.

    Args:
        grid: Complete active AOD grid after loading.
        bindings: Ordered (atom ID, row ID, column ID) transfers.
        duration_us: Transfer duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    grid: AODGrid
    bindings: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class Move(_TimedAction):
    """Move an AOD grid and record the affected atoms' destinations.

    Args:
        grid: Complete active AOD grid at the end of the movement.
        targets: Ordered (atom ID, destination) pairs for affected atoms.
        duration_us: Movement duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    grid: AODGrid
    targets: tuple[tuple[int, Position], ...]


@dataclass(frozen=True, slots=True)
class Store(_TimedAction):
    """Transfer atoms from AOD intersections into SLM traps.

    Args:
        grid: Complete active AOD grid remaining after unloading.
        bindings: Ordered (atom ID, SLM site ID) transfers.
        duration_us: Transfer duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    grid: AODGrid
    bindings: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class CZPulse(_TimedAction):
    """Apply a CZ pulse to a region or an addressed collection of atoms.

    Addressed atoms describe the illuminated set, not an ordered list of pairs.

    Args:
        channel: Device control channel used for the pulse.
        coverage: Illuminated region or ordered addressed atom IDs.
        duration_us: Pulse duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    channel: str
    coverage: Region | tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Rotation(_TimedAction):
    """Apply one addressed single-qubit rotation to each target atom.

    The unitary is exp(-i * angle * Pauli_axis / 2).

    Args:
        channel: Device control channel used for the rotation.
        atoms: Ordered target atom IDs.
        axis: Rotation axis, X, Y, or Z.
        angle: Rotation angle in radians.
        duration_us: Pulse duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative, or angle is nonfinite.
    """

    channel: str
    atoms: tuple[int, ...]
    axis: Literal["X", "Y", "Z"]
    angle: float

    def __post_init__(self) -> None:
        _TimedAction.__post_init__(self)
        if not isfinite(self.angle):
            raise ValueError("Rotation angle must be finite.")


@dataclass(frozen=True, slots=True)
class Reset(_TimedAction):
    """Reset addressed atoms to the computational zero state.

    Args:
        channel: Device control channel used for reset.
        atoms: Ordered target atom IDs.
        duration_us: Reset duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    channel: str
    atoms: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Measure(_TimedAction):
    """Measure atoms in the Z basis and bind stable physical measurement IDs.

    Other measurement bases are expressed with preceding rotations. If
    retain_atoms is false, measured atoms are removed at the end of readout.

    Args:
        channel: Device readout channel.
        results: Ordered (atom ID, result reference) outputs.
        retain_atoms: Whether measured atoms remain available afterward.
        duration_us: Measurement duration in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """

    channel: str
    results: tuple[tuple[int, ResultRef], ...]
    retain_atoms: bool


@dataclass(frozen=True, slots=True)
class Wait(_TimedAction):
    """Reserve time for a delay, including measurement feedback latency.

    Args:
        duration_us: Delay in microseconds.
        source_ids: Physical operation IDs served by this action.

    Raises:
        ValueError: If duration_us is nonfinite or negative.
    """


type AtomAction = Load | Move | Store | CZPulse | Rotation | Reset | Measure | Wait


@dataclass(frozen=True, slots=True)
class AtomStep:
    """Actions selected by the compiler to start together.

    Every action is interpreted against the same step-entry state. Tuple order
    controls serialization, not execution order.

    Args:
        actions: Ordered parallel actions. The next step starts when all finish.
    """

    actions: tuple[AtomAction, ...] = ()

    @property
    def duration_us(self) -> float:
        """Return the longest action duration, or zero for an empty step."""
        return max((action.duration_us for action in self.actions), default=0)


@dataclass(frozen=True, slots=True)
class AtomProgram:
    """A sequential collection of parallel atom-action steps.

    Args:
        steps: Ordered parallel steps.
        global_phase: Circuit global phase in radians.

    Raises:
        ValueError: If global_phase is nonfinite.
    """

    steps: tuple[AtomStep, ...] = ()
    global_phase: float = 0.0

    def __post_init__(self) -> None:
        if not isfinite(self.global_phase):
            raise ValueError("Program global_phase must be finite.")

    @property
    def duration_us(self) -> float:
        """Return the sum of step durations in microseconds."""
        return sum((step.duration_us for step in self.steps), start=0.0)

    def timeline(self) -> tuple[tuple[float, AtomStep], ...]:
        """Return ordered (start time in microseconds, step) entries."""
        entries: list[tuple[float, AtomStep]] = []
        start = 0.0
        for step in self.steps:
            entries.append((start, step))
            start += step.duration_us
        return tuple(entries)

    def to_text(self) -> str:
        """Return deterministic Stim-style text, with TICK closing every step."""
        lines = []
        if self.global_phase != 0:
            lines.append(f"# global_phase={self.global_phase!r}")
        for step in self.steps:
            lines.extend(_action_text(action) for action in step.actions)
            lines.append(f"TICK(duration_us={step.duration_us!r})")
        return "\n".join(lines) + "\n" if lines else ""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _grid_parameters(grid: AODGrid) -> str:
    rows = [(line.id, line.coordinate) for line in grid.rows]
    columns = [(line.id, line.coordinate) for line in grid.columns]
    return f"aod={_json(grid.aod_id)},rows={_json(rows)},columns={_json(columns)}"


def _transport_text(action: Load | Move | Store, parameters: str) -> str:
    parameters += "," + _grid_parameters(action.grid)
    if isinstance(action, Load):
        name = "LOAD"
        targets = [f"{atom}->({row},{column})" for atom, row, column in action.bindings]
    elif isinstance(action, Move):
        name = "MOVE"
        targets = [
            f"{atom}->({position.x!r},{position.y!r})"
            for atom, position in action.targets
        ]
    else:
        name = "STORE"
        targets = [f"{atom}->{_json(site)}" for atom, site in action.bindings]
    return " ".join((f"{name}({parameters})", *targets))


def _pulse_text(
    action: CZPulse | Rotation | Reset | Measure | Wait, parameters: str
) -> str:
    if isinstance(action, Wait):
        return f"WAIT({parameters})"
    parameters += f",channel={_json(action.channel)}"
    targets: list[str]
    if isinstance(action, CZPulse):
        name = "CZ_PULSE"
        if isinstance(action.coverage, Region):
            region = action.coverage
            bounds = [region.x_min, region.y_min, region.x_max, region.y_max]
            parameters += f",region={_json(bounds)}"
            targets = []
        else:
            targets = [str(atom) for atom in action.coverage]
    elif isinstance(action, Rotation):
        name = "ROT"
        parameters += f",axis={_json(action.axis)},angle={action.angle!r}"
        targets = [str(atom) for atom in action.atoms]
    elif isinstance(action, Reset):
        name = "R"
        targets = [str(atom) for atom in action.atoms]
    elif isinstance(action, Measure):
        name = "M"
        parameters += f",retain_atoms={_json(action.retain_atoms)}"
        targets = [
            f"{atom}->rec[{result.measurement_id}]" for atom, result in action.results
        ]
    else:
        assert_never(action)
    return " ".join((f"{name}({parameters})", *targets))


def _action_text(action: AtomAction) -> str:
    parameters = f"duration_us={action.duration_us!r}"
    if isinstance(action, Load | Move | Store):
        text = _transport_text(action, parameters)
    else:
        text = _pulse_text(action, parameters)
    if action.source_ids:
        text += " # source_ids=" + ",".join(str(source) for source in action.source_ids)
    return text
