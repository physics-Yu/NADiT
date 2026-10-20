"""Bind selected operations to atom IDs, device channels, and durations."""

from typing import Literal

from ..atom_ir import (
    AtomAction,
    AtomStep,
    CZPulse,
    GlobalRY,
    LocalU,
    Measure,
    Reset,
    Rotation,
)
from ..contracts import AtomFragment, GateLayer
from ..device import Device, Region
from ..physical_ir import PhysicalDAG, PhysicalOperation
from ..state import AtomLayout

_ROTATION_AXES: dict[str, Literal["X", "Y", "Z"]] = {"rx": "X", "ry": "Y", "rz": "Z"}


def emit_layer(
    dag: PhysicalDAG, layer: GateLayer, layout: AtomLayout, device: Device
) -> AtomFragment:
    """Construct one parallel Atom IR step from an already placed layer.

    Args:
        dag: Native operations and stable measurement identities.
        layer: Operations selected for parallel execution.
        layout: Arrangement prepared by placement and transport.
        device: Control channels and operation durations.

    Returns:
        Atom IR actions, completed native node IDs, and the retained layout.

    Raises:
        ValueError: If an operation or required control capability is unsupported.
    """
    actions = tuple(
        _emit_operation(dag.nodes[operation_id], layout, device)
        for operation_id in layer.operation_ids
    )
    steps = (AtomStep(actions),) if actions else ()
    return AtomFragment(steps, layout, layer.operation_ids)


def _emit_operation(
    operation: PhysicalOperation, layout: AtomLayout, device: Device
) -> AtomAction:
    atom_ids = tuple(layout.qubit_to_atom[q] for q in operation.qubits)
    sources = (operation.id,)
    if operation.name == "global_ry":
        if device.global_control is None:
            raise ValueError("Global RY emission requires a global-control capability.")
        bounds = tuple(zone.bounds for zone in device.zones)
        coverage = Region(
            min(region.x_min for region in bounds),
            min(region.y_min for region in bounds),
            max(region.x_max for region in bounds),
            max(region.y_max for region in bounds),
        )
        if device.global_control.region is not None:
            coverage = Region(*device.global_control.region)
        return GlobalRY(
            device.global_control.channel,
            coverage,
            operation.params[0],
            duration_us=device.global_control.rotation_us,
            source_ids=sources,
        )
    if operation.name == "cz":
        if device.cz is None:
            raise ValueError("CZ emission requires a CZ capability.")
        positions = tuple(layout.atom(atom_id).position for atom_id in atom_ids)
        coverage = Region(
            min(point.x for point in positions),
            min(point.y for point in positions),
            max(point.x for point in positions),
            max(point.y for point in positions),
        )
        return CZPulse(
            device.cz.channel,
            coverage,
            duration_us=device.cz.duration_us,
            source_ids=sources,
        )
    if operation.name == "measure":
        if device.readout is None or not device.readout.retain_atoms:
            raise ValueError("Measurement emission requires retained-atom readout.")
        result = operation.result
        assert result is not None
        return Measure(
            device.readout.channel,
            ((atom_ids[0], result),),
            True,
            duration_us=device.readout.duration_us,
            source_ids=sources,
        )
    if operation.name in (*_ROTATION_AXES, "u", "reset"):
        return _emit_local(operation, atom_ids, device)
    raise ValueError(f"Unsupported Atom IR emission operation {operation.name!r}.")


def _emit_local(
    operation: PhysicalOperation, atom_ids: tuple[int, ...], device: Device
) -> Rotation | LocalU | Reset:
    if device.local is None:
        raise ValueError("Addressed operation emission requires local control.")
    if operation.name == "reset":
        return Reset(
            device.local.channel,
            atom_ids,
            duration_us=device.local.reset_us,
            source_ids=(operation.id,),
        )
    if operation.name == "u":
        return LocalU(
            device.local.channel,
            atom_ids,
            *operation.params,
            duration_us=device.local.rotation_us,
            source_ids=(operation.id,),
        )
    return Rotation(
        device.local.channel,
        atom_ids,
        _ROTATION_AXES[operation.name],
        operation.params[0],
        duration_us=device.local.rotation_us,
        source_ids=(operation.id,),
    )
