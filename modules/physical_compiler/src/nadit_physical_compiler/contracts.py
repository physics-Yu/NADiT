"""Small handoff records and structural interfaces for compiler algorithms."""

from dataclasses import dataclass
from typing import Protocol

from .atom_ir import AtomStep
from .device import Device
from .physical_ir import PhysicalDAG
from .state import AtomLayout


@dataclass(frozen=True, slots=True)
class GateLayer:
    """Native DAG operations selected for one compilation layer.

    Args:
        operation_ids: Node IDs in the native PhysicalDAG. The caller resolves
            conditions and supplies ready operations before selection.
    """

    operation_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlacementPlan:
    """Selected atom arrangements before and after a gate layer.

    Args:
        execution_layout: Atom positions and holders for executing the layer.
        exit_layout: Positions and holders to retain after the layer.
    """

    execution_layout: AtomLayout
    exit_layout: AtomLayout


@dataclass(frozen=True, slots=True)
class AtomFragment:
    """Selected atom steps and their resulting layout.

    Args:
        steps: Existing Atom IR steps, retained without copying or validation.
        exit_layout: Layout after the selected steps.
        completed_operation_ids: Native DAG node IDs completed by the fragment.
            Transport alone does not complete a circuit operation.
    """

    steps: tuple[AtomStep, ...]
    exit_layout: AtomLayout
    completed_operation_ids: tuple[int, ...] = ()


class Scheduler(Protocol):
    """Select a gate layer from the compiler's resolved ready operations."""

    def select(self, dag: PhysicalDAG, ready: tuple[int, ...]) -> GateLayer:
        """Choose the next layer.

        Args:
            dag: Native physical operations and dependencies.
            ready: Node IDs whose dependencies and conditions are resolved.

        Returns:
            Selected operations from ready, using the same node IDs.
        """
        ...


class ReuseAnalyzer(Protocol):
    """Identify physical qubits that can remain for a following gate layer."""

    def analyze(
        self, dag: PhysicalDAG, current: GateLayer, following: GateLayer
    ) -> tuple[int, ...]:
        """Find reuse candidates between two selected layers.

        Args:
            dag: Native physical operations and dependencies.
            current: Layer being placed.
            following: Known following layer used for lookahead.

        Returns:
            Physical-qubit IDs considered for reuse, before atom binding.
        """
        ...


class Placer(Protocol):
    """Choose execution and exit arrangements using configured search options."""

    def place(
        self,
        dag: PhysicalDAG,
        layer: GateLayer,
        layout: AtomLayout,
        device: Device,
        *,
        reuse_qubits: tuple[int, ...] = (),
    ) -> PlacementPlan:
        """Choose layouts for a selected gate layer.

        Args:
            dag: Native physical operations and dependencies.
            layer: Layer to place.
            layout: Current atom arrangement and physical-qubit binding.
            device: Device geometry, timing, and capabilities.
            reuse_qubits: Physical-qubit IDs proposed for reuse.

        Returns:
            Selected execution and exit layouts.
        """
        ...


class Transporter(Protocol):
    """Construct transport steps using one implementation's movement rules."""

    def construct(
        self, layout: AtomLayout, target_layout: AtomLayout, device: Device
    ) -> AtomFragment:
        """Construct a selected transport fragment.

        Args:
            layout: Current atom positions, holders, and active AOD lines.
            target_layout: Requested positions, holders, and active AOD lines.
            device: Device geometry, timing, and capabilities.

        Returns:
            Constructed Atom IR steps and their resulting layout.
        """
        ...
