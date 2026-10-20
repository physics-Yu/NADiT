"""Emit one region pulse for a complete placed CZ layer."""

from dataclasses import dataclass

from ..atom_ir import AtomStep, CZPulse
from ..contracts import AtomFragment, GateLayer
from ..device import Device, Region
from ..physical_ir import PhysicalDAG
from ..state import AtomLayout
from .actions import emit_layer


@dataclass(frozen=True, slots=True)
class WholeLayerEmitter:
    """Bind a placed CZ layer to one configured illumination region.

    Args:
        coverage: Region supplied by the fixed interaction-site catalog.
            Placement establishes which atoms occupy the illuminated positions.
    """

    coverage: Region

    def __call__(
        self, dag: PhysicalDAG, layer: GateLayer, layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Emit a single CZ pulse, or delegate an addressed/readout layer.

        Args:
            dag: Native physical operations.
            layer: Selected homogeneous layer.
            layout: Atom arrangement prepared for the layer.
            device: Operation channels and durations.

        Returns:
            Constructed actions, all completed node IDs, and the unchanged layout.

        Raises:
            ValueError: If a required device capability or operation is unsupported.
        """
        if not layer.operation_ids:
            return AtomFragment((), layout)
        if not all(dag.nodes[index].name == "cz" for index in layer.operation_ids):
            return emit_layer(dag, layer, layout, device)
        if device.cz is None:
            raise ValueError("CZ emission requires a CZ capability.")
        pulse = CZPulse(
            device.cz.channel,
            self.coverage,
            duration_us=device.cz.duration_us,
            source_ids=layer.operation_ids,
        )
        return AtomFragment((AtomStep((pulse,)),), layout, layer.operation_ids)
