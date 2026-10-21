"""Export existing Atom IR and replay boundaries as an inspectable trace."""

from bisect import bisect_left
from collections.abc import Sequence
from itertools import combinations
from math import hypot
from typing import Any

from ..atom_ir import (
    AtomAction,
    AtomProgram,
    AtomStep,
    CZPulse,
    GlobalRY,
    Load,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
    Store,
)
from ..compiler import CompiledOperation, MeasurementRecord
from ..coverage import RegionSet
from ..device import Device, Region
from ..replay import apply_step
from ..state import (
    COORDINATE_TOLERANCE,
    AODGrid,
    AtomLayout,
    AtomState,
    SLMHolder,
)

type _Record = dict[str, Any]


def build_trace(
    program: AtomProgram,
    entry_state: AtomLayout,
    device: Device,
    *,
    title: str,
    description: str = "",
    measurements: Sequence[MeasurementRecord] = (),
    operations: Sequence[CompiledOperation] = (),
) -> _Record:
    """Build a JSON-compatible trace without compiling or sampling results.

    Args:
        program: Constructed Atom IR with parallel action durations.
        entry_state: Initial atom identities, positions, holders, and grids.
        device: Geometry and CZ interaction capability.
        title: Display name for this trace.
        description: Optional explanation of the compiled case.
        measurements: Already sampled results and their availability times.
        operations: Compiler decisions and native operation intervals.

    Returns:
        Version-one trace with sparse completion patches and endpoint
        occupied-intersection observations. The duration includes pending
        measurement availability beyond the final action.

    Raises:
        NotImplementedError: For destructive readout, as in apply_step.
        KeyError: If replay encounters an absent atom or SLM site.
    """
    qubits = {atom: qubit for qubit, atom in enumerate(entry_state.qubit_to_atom)}
    steps: list[_Record] = []
    issues = _observations(entry_state, -1, 0.0)
    layout = entry_state
    action_index = 0
    for step_index, (start, step) in enumerate(program.timeline()):
        exit_state = apply_step(layout, step, device)
        entry_atoms = {atom.id: atom for atom in layout.atoms}
        exit_atoms = {atom.id: atom for atom in exit_state.atoms}
        actions = [
            _action(
                action,
                action_index + offset,
                layout,
                entry_atoms,
                exit_atoms,
                device,
                qubits,
            )
            for offset, action in enumerate(step.actions)
        ]
        steps.append(
            {
                "index": step_index,
                "start_us": start,
                "duration_us": step.duration_us,
                "actions": actions,
            }
        )
        action_index += len(actions)
        issues.extend(_observations(exit_state, step_index, start + step.duration_us))
        layout = exit_state
    return {
        "schema_version": 1,
        "title": title,
        "description": description,
        "duration_us": max(
            program.duration_us,
            max((item.available_at_us for item in measurements), default=0.0),
        ),
        "program_duration_us": program.duration_us,
        "device": _device(device),
        "initial": {
            "atoms": [_atom(atom, qubits) for atom in entry_state.atoms],
            "grids": [_grid(grid) for grid in entry_state.aod_grids],
        },
        "steps": steps,
        "measurements": [
            {
                "measurement_id": item.result.measurement_id,
                "value": item.value,
                "available_at_us": item.available_at_us,
            }
            for item in measurements
        ],
        "operations": [
            {
                "operation_id": item.operation_id,
                "status": item.status,
                "start_us": item.start_us,
                "end_us": item.end_us,
            }
            for item in operations
        ],
        "issues": issues,
        "atom_ir": program.to_text(),
    }


def _bounds(region: Region) -> list[float]:
    return [region.x_min, region.y_min, region.x_max, region.y_max]


def _device(device: Device) -> _Record:
    return {
        "name": device.name,
        "zones": [
            {
                "id": zone.id,
                "bounds": _bounds(zone.bounds),
                "operations": list(zone.operations),
            }
            for zone in device.zones
        ],
        "slm_sites": [
            {"id": site.id, "x": site.position.x, "y": site.position.y}
            for site in device.slm_sites
        ],
        "aods": [
            {
                "id": aod.id,
                "workspace": _bounds(aod.workspace),
                "min_spacing": aod.min_spacing,
                "max_rows": aod.max_rows,
                "max_columns": aod.max_columns,
            }
            for aod in device.aods
        ],
        "cz_radius_um": device.cz.radius_um if device.cz is not None else None,
    }


def _atom(atom: AtomState, qubits: dict[int, int]) -> _Record:
    holder = atom.holder
    return {
        "id": atom.id,
        "qubit": qubits.get(atom.id),
        "x": atom.position.x,
        "y": atom.position.y,
        "holder": (
            {"kind": "slm", "site_id": holder.site_id}
            if isinstance(holder, SLMHolder)
            else {
                "kind": "aod",
                "aod_id": holder.aod_id,
                "row": holder.row,
                "column": holder.column,
            }
        ),
    }


def _grid(grid: AODGrid) -> _Record:
    return {
        "aod_id": grid.aod_id,
        "rows": [{"id": line.id, "coordinate": line.coordinate} for line in grid.rows],
        "columns": [
            {"id": line.id, "coordinate": line.coordinate} for line in grid.columns
        ],
    }


def _action_atoms(action: AtomAction, layout: AtomLayout) -> tuple[int, ...]:
    if isinstance(action, Load | Store):
        return tuple(binding[0] for binding in action.bindings)
    if isinstance(action, Move):
        return tuple(atom for atom, _ in action.targets)
    if isinstance(action, Rotation | LocalU | Reset):
        return action.atoms
    if isinstance(action, Measure):
        return tuple(atom for atom, _ in action.results)
    if isinstance(action, CZPulse | GlobalRY):
        coverage = action.coverage
        if isinstance(coverage, tuple):
            return coverage
        return tuple(
            atom.id for atom in layout.atoms if coverage.contains(atom.position)
        )
    return ()


def _action(
    action: AtomAction,
    index: int,
    layout: AtomLayout,
    entry_atoms: dict[int, AtomState],
    exit_atoms: dict[int, AtomState],
    device: Device,
    qubits: dict[int, int],
) -> _Record:
    atoms = _action_atoms(action, layout)
    patch: _Record = {"atoms": [], "grids": []}
    if isinstance(action, Load | Move | Store):
        patch["atoms"] = [
            _atom(exit_atoms[atom], qubits)
            for atom in atoms
            if entry_atoms[atom] != exit_atoms[atom]
        ]
        if action.grid != layout.grid(action.grid.aod_id):
            patch["grids"] = [_grid(action.grid)]
    return {
        "index": index,
        "kind": type(action).__name__,
        "duration_us": action.duration_us,
        "source_ids": list(action.source_ids),
        "atoms": list(atoms),
        "text": AtomProgram((AtomStep((action,)),)).to_text().splitlines()[0],
        "parameters": _parameters(action),
        "coverage": _coverage(action, atoms),
        "pairs": _pairs(atoms, entry_atoms, device)
        if isinstance(action, CZPulse)
        else [],
        "patch": patch,
    }


def _parameters(action: AtomAction) -> _Record:
    if isinstance(action, Load | Store):
        return {
            "aod_id": action.grid.aod_id,
            "bindings": [list(binding) for binding in action.bindings],
        }
    if isinstance(action, Move):
        return {
            "aod_id": action.grid.aod_id,
            "targets": [
                {"atom_id": atom, "x": position.x, "y": position.y}
                for atom, position in action.targets
            ],
        }
    if isinstance(action, Rotation):
        return {"channel": action.channel, "axis": action.axis, "angle": action.angle}
    if isinstance(action, LocalU):
        return {
            "channel": action.channel,
            "theta": action.theta,
            "phi": action.phi,
            "lam": action.lam,
        }
    if isinstance(action, GlobalRY):
        return {"channel": action.channel, "angle": action.angle}
    if isinstance(action, Measure):
        return {
            "channel": action.channel,
            "results": [
                {"atom_id": atom, "measurement_id": result.measurement_id}
                for atom, result in action.results
            ],
            "retain_atoms": action.retain_atoms,
        }
    if isinstance(action, CZPulse | Reset):
        return {"channel": action.channel}
    return {}


def _coverage(action: AtomAction, atoms: tuple[int, ...]) -> _Record | None:
    if not isinstance(action, CZPulse | GlobalRY):
        return None
    coverage = action.coverage
    regions = (
        (coverage,)
        if isinstance(coverage, Region)
        else coverage.regions
        if isinstance(coverage, RegionSet)
        else ()
    )
    return {"regions": [_bounds(region) for region in regions], "atoms": list(atoms)}


def _pairs(
    atoms: tuple[int, ...], entry: dict[int, AtomState], device: Device
) -> list[list[int]]:
    if device.cz is None:
        return []
    allowed = {frozenset(pair) for pair in device.cz.holder_pairs}
    pairs = []
    for first, second in combinations(atoms, 2):
        a, b = entry[first], entry[second]
        holders = frozenset(
            "slm" if isinstance(atom.holder, SLMHolder) else "aod" for atom in (a, b)
        )
        if (
            holders in allowed
            and hypot(a.position.x - b.position.x, a.position.y - b.position.y)
            <= device.cz.radius_um
        ):
            pairs.append([first, second])
    return pairs


def _has_coordinate(coordinates: list[float], value: float) -> bool:
    index = bisect_left(coordinates, value - COORDINATE_TOLERANCE)
    return (
        index < len(coordinates)
        and abs(coordinates[index] - value) <= COORDINATE_TOLERANCE
    )


def _observations(layout: AtomLayout, step_index: int, time_us: float) -> list[_Record]:
    issues = []
    for grid in layout.aod_grids:
        rows = sorted(line.coordinate for line in grid.rows)
        columns = sorted(line.coordinate for line in grid.columns)
        for atom in layout.atoms:
            if (
                isinstance(atom.holder, SLMHolder)
                and _has_coordinate(rows, atom.position.y)
                and _has_coordinate(columns, atom.position.x)
            ):
                issues.append(
                    {
                        "step_index": step_index,
                        "time_us": time_us,
                        "kind": "occupied_intersection",
                        "atom_id": atom.id,
                        "aod_id": grid.aod_id,
                        "x": atom.position.x,
                        "y": atom.position.y,
                        "message": (
                            f"SLM atom {atom.id} lies on an active intersection "
                            f"of AOD {grid.aod_id} at this boundary."
                        ),
                    }
                )
    return issues
