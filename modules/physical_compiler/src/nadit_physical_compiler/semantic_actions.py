"""Independent pulse, addressing, and readout checks against a native DAG."""

from collections import Counter
from itertools import combinations
from math import hypot

from .atom_ir import (
    AtomAction,
    AtomStep,
    CZPulse,
    GlobalRY,
    LocalU,
    Measure,
    Move,
    Reset,
    Rotation,
)
from .capabilities import GlobalControl, LocalControl, ReadoutCapability
from .coverage import RegionSet
from .device import Device, Region
from .motion import ConstraintResult
from .physical_ir import PhysicalDAG, PhysicalOperation
from .semantic_geometry import contains, intersects_segment
from .state import COORDINATE_TOLERANCE, AODHolder, AtomLayout, AtomState

type AddressedAction = Rotation | LocalU | GlobalRY | Reset | Measure
type SemanticAction = CZPulse | AddressedAction


def check_action_semantics(
    dag: PhysicalDAG,
    device: Device,
    layout: AtomLayout,
    action: AtomAction,
    step: AtomStep,
) -> tuple[ConstraintResult, ...]:
    """Return pulse/addressing failures against the unchanged step-entry layout.

    Args:
        dag: Native operation identities, qubits, parameters and measurements.
        device: Operation capabilities and allowed geometric zones.
        layout: Atom positions and physical-qubit bindings at step entry.
        action: Action being checked, belonging to ``step``.
        step: All actions starting together, including moves and readout.

    Returns:
        Every violated rule in deterministic check order, or an empty tuple.
        Motion and delay actions are handled by the transport/timing checks.
        Cross-action readout spacing is reported on the later Measure action.
    """
    if not isinstance(action, CZPulse | Rotation | LocalU | GlobalRY | Reset | Measure):
        return ()
    failures: list[ConstraintResult] = []
    sources = _sources(dag, layout, action, failures)
    if isinstance(action, CZPulse):
        _check_cz(device, layout, action, sources, step, failures)
        _check_concurrent_cz(dag, device, layout, action, step, failures)
    else:
        _check_addressed(device, layout, action, sources, step, failures)
    return tuple(failures)


def _sources(
    dag: PhysicalDAG,
    layout: AtomLayout,
    action: SemanticAction,
    failures: list[ConstraintResult],
) -> tuple[PhysicalOperation, ...]:
    sources = []
    for index in action.source_ids:
        if not 0 <= index < len(dag.nodes):
            failures.append(
                ConstraintResult("addressing", f"Unknown source operation {index}.")
            )
            continue
        operation = dag.nodes[index]
        if any(qubit >= len(layout.qubit_to_atom) for qubit in operation.qubits):
            failures.append(
                ConstraintResult(
                    "addressing",
                    f"Operation {index} has no complete qubit-to-atom binding.",
                )
            )
            continue
        if not _matches_operation(action, operation):
            failures.append(
                ConstraintResult(
                    "addressing",
                    f"Action kind or parameters disagree with source {index}.",
                )
            )
            continue
        sources.append(operation)
    if len(set(action.source_ids)) != len(action.source_ids):
        failures.append(
            ConstraintResult("addressing", "Action repeats a source operation.")
        )
    return tuple(sources)


def _matches_operation(action: SemanticAction, source: PhysicalOperation) -> bool:
    if isinstance(action, CZPulse):
        return (
            source.name == "cz"
            and len(source.qubits) == len(set(source.qubits)) == 2
            and not source.params
        )
    if isinstance(action, GlobalRY):
        return source.name == "global_ry" and source.params == (action.angle,)
    if len(source.qubits) != 1:
        return False
    if isinstance(action, Rotation):
        return source.name == f"r{action.axis.lower()}" and source.params == (
            action.angle,
        )
    if isinstance(action, LocalU):
        return source.name == "u" and source.params == (
            action.theta,
            action.phi,
            action.lam,
        )
    return (
        source.name == ("reset" if isinstance(action, Reset) else "measure")
        and not source.params
    )


def _illuminated(
    layout: AtomLayout, coverage: Region | RegionSet | tuple[int, ...]
) -> tuple[int, ...]:
    if isinstance(coverage, tuple):
        return coverage
    return tuple(atom.id for atom in layout.atoms if contains(coverage, atom.position))


def _check_cz(
    device: Device,
    layout: AtomLayout,
    action: CZPulse,
    sources: tuple[PhysicalOperation, ...],
    step: AtomStep,
    failures: list[ConstraintResult],
) -> None:
    capability = device.cz
    if capability is None:
        failures.append(
            ConstraintResult("zone_capability", "Device has no CZ capability.")
        )
        return
    if action.channel != capability.channel:
        failures.append(
            ConstraintResult("addressing", "CZ pulse uses the wrong device channel.")
        )
    atoms = {atom.id: atom for atom in layout.atoms}
    illuminated = _illuminated(layout, action.coverage)
    if (
        len(set(illuminated)) != len(illuminated)
        or not set(illuminated) <= atoms.keys()
    ):
        failures.append(
            ConstraintResult(
                "addressing", "CZ coverage repeats or names unknown atoms."
            )
        )
    illuminated_set = set(illuminated) & atoms.keys()
    expected = Counter(
        tuple(sorted(layout.qubit_to_atom[q] for q in source.qubits))
        for source in sources
    )
    if any(count != 1 for count in expected.values()):
        failures.append(
            ConstraintResult(
                "addressing", "One CZ pulse cannot execute a pair more than once."
            )
        )
    actual = {
        (left, right)
        for left, right in combinations(sorted(illuminated_set), 2)
        if _distance(atoms[left], atoms[right])
        <= capability.radius_um + COORDINATE_TOLERANCE
    }
    for pair in sorted(expected.keys() - actual):
        failures.append(
            ConstraintResult(
                "missing_interaction", f"CZ pulse does not couple atoms {pair}."
            )
        )
    for pair in sorted(actual - expected.keys()):
        failures.append(
            ConstraintResult(
                "unintended_interaction", f"CZ pulse also couples atoms {pair}."
            )
        )
    expected_atoms = {atom for pair in expected for atom in pair}
    for atom in (
        sorted(illuminated_set - expected_atoms)
        if not capability.allow_spectators
        else ()
    ):
        failures.append(
            ConstraintResult(
                "illuminated_spectator", f"CZ pulse illuminates spectator atom {atom}."
            )
        )
    for left, right in sorted(expected):
        pair_atoms = (atoms[left], atoms[right])
        holder_pair = tuple(sorted(_holder(atom) for atom in pair_atoms))
        if not any(
            holder_pair == tuple(sorted(allowed)) for allowed in capability.holder_pairs
        ):
            failures.append(
                ConstraintResult(
                    "holder_pair",
                    f"CZ pair {(left, right)} uses disallowed holders {holder_pair}.",
                )
            )
        if not any(
            "cz" in zone.operations
            and all(contains(zone.bounds, atom.position) for atom in pair_atoms)
            for zone in device.zones
        ):
            failures.append(
                ConstraintResult(
                    "zone_capability",
                    f"CZ pair {(left, right)} is outside a shared CZ-capable zone.",
                )
            )
    _moving_in_pulse(layout, action, step, failures)


def _check_concurrent_cz(
    dag: PhysicalDAG,
    device: Device,
    layout: AtomLayout,
    action: CZPulse,
    step: AtomStep,
    failures: list[ConstraintResult],
) -> None:
    capability = device.cz
    if capability is None or action.channel != capability.channel:
        return
    pulses = tuple(
        candidate
        for candidate in step.actions
        if isinstance(candidate, CZPulse) and candidate.channel == action.channel
    )
    atoms = {atom.id: atom for atom in layout.atoms}
    previous: set[int] = set()
    for pulse in pulses:
        if pulse is action:
            break
        previous.update(_illuminated(layout, pulse.coverage))
    previous.intersection_update(atoms)
    current = set(_illuminated(layout, action.coverage)) & atoms.keys()
    # Each pulse's internal edges are checked above. New cross-coverage edges
    # appear when this pulse adds atoms to the same-channel illuminated union.
    cross_pairs = {
        tuple(sorted((left, right)))
        for left in previous - current
        for right in current - previous
    }
    if not cross_pairs:
        return
    expected = {
        tuple(sorted(layout.qubit_to_atom[q] for q in source.qubits))
        for pulse in pulses
        for source in _sources(dag, layout, pulse, [])
    }
    for left, right in sorted(cross_pairs - expected):
        if (
            _distance(atoms[left], atoms[right])
            <= capability.radius_um + COORDINATE_TOLERANCE
        ):
            failures.append(
                ConstraintResult(
                    "unintended_interaction",
                    f"Concurrent CZ pulses also couple atoms {(left, right)}.",
                )
            )


def _moving_in_pulse(
    layout: AtomLayout,
    pulse: CZPulse,
    step: AtomStep,
    failures: list[ConstraintResult],
) -> None:
    atoms = {atom.id: atom for atom in layout.atoms}
    coverage = pulse.coverage
    regions: tuple[Region, ...]
    if isinstance(coverage, Region):
        regions = (coverage,)
    elif isinstance(coverage, RegionSet):
        regions = coverage.regions
    else:
        regions = tuple(
            Region(
                atoms[atom].position.x,
                atoms[atom].position.y,
                atoms[atom].position.x,
                atoms[atom].position.y,
            )
            for atom in coverage
            if atom in atoms
        )
    for action in step.actions:
        if not isinstance(action, Move):
            continue
        for atom_id, target in action.targets:
            if atom_id in atoms and any(
                intersects_segment(region, atoms[atom_id].position, target)
                for region in regions
            ):
                failures.append(
                    ConstraintResult(
                        "moving_in_pulse",
                        f"Atom {atom_id} moves through concurrent CZ coverage.",
                    )
                )


def _check_addressed(
    device: Device,
    layout: AtomLayout,
    action: AddressedAction,
    sources: tuple[PhysicalOperation, ...],
    step: AtomStep,
    failures: list[ConstraintResult],
) -> None:
    addressed = _addressed_atoms(layout, action)
    expected = [layout.qubit_to_atom[q] for source in sources for q in source.qubits]
    if Counter(addressed) != Counter(expected) or len(set(addressed)) != len(addressed):
        failures.append(
            ConstraintResult(
                "addressing",
                "Addressed atoms do not equal the source-operation qubits.",
            )
        )
    if isinstance(action, Measure):
        expected_results = Counter(
            (layout.qubit_to_atom[source.qubits[0]], source.result)
            for source in sources
        )
        if Counter(action.results) != expected_results:
            failures.append(
                ConstraintResult(
                    "result_binding",
                    "Readout atom/result bindings disagree with source measurements.",
                )
            )
    _check_capability(device, layout, action, addressed, failures)
    if isinstance(action, Measure):
        _check_readout_spacing(device, layout, action, step, failures)


def _addressed_atoms(layout: AtomLayout, action: AddressedAction) -> tuple[int, ...]:
    if isinstance(action, GlobalRY):
        return _illuminated(layout, action.coverage)
    if isinstance(action, Measure):
        return tuple(atom for atom, _ in action.results)
    return action.atoms


def _check_capability(
    device: Device,
    layout: AtomLayout,
    action: AddressedAction,
    addressed: tuple[int, ...],
    failures: list[ConstraintResult],
) -> None:
    capability: GlobalControl | LocalControl | ReadoutCapability | None
    if isinstance(action, GlobalRY):
        capability = device.global_control
        operation = "rotate"
    elif isinstance(action, Measure):
        capability = device.readout
        operation = "measure"
    else:
        capability = device.local
        operation = "reset" if isinstance(action, Reset) else "rotate"
    if capability is None:
        failures.append(
            ConstraintResult(
                "zone_capability", f"Device has no {operation} capability."
            )
        )
        return
    if action.channel != capability.channel:
        failures.append(
            ConstraintResult(
                "addressing", f"{operation} uses the wrong device channel."
            )
        )
    if (
        isinstance(action, Measure)
        and device.readout is not None
        and action.retain_atoms != device.readout.retain_atoms
    ):
        failures.append(
            ConstraintResult(
                "addressing", "Readout retention disagrees with the device capability."
            )
        )
    atoms = {atom.id: atom for atom in layout.atoms}
    for atom_id in addressed:
        if atom_id not in atoms:
            failures.append(
                ConstraintResult(
                    "addressing", f"Addressed atom {atom_id} is absent from the layout."
                )
            )
            continue
        atom = atoms[atom_id]
        if not any(
            operation in zone.operations and contains(zone.bounds, atom.position)
            for zone in device.zones
        ):
            failures.append(
                ConstraintResult(
                    "zone_capability",
                    f"Atom {atom_id} is outside a zone permitting {operation}.",
                )
            )
        if _holder(atom) not in capability.holders:
            failures.append(
                ConstraintResult(
                    "holder",
                    f"Atom {atom_id} uses a disallowed holder for {operation}.",
                )
            )


def _check_readout_spacing(
    device: Device,
    layout: AtomLayout,
    action: Measure,
    step: AtomStep,
    failures: list[ConstraintResult],
) -> None:
    if device.readout is None:
        return
    if device.readout.max_atoms is not None:
        total = sum(
            len(candidate.results)
            for candidate in step.actions
            if isinstance(candidate, Measure) and candidate.channel == action.channel
        )
        if total > device.readout.max_atoms:
            failures.append(
                ConstraintResult(
                    "readout_capacity", "Concurrent readout exceeds device.max_atoms."
                )
            )
    atoms = {atom.id: atom for atom in layout.atoms}
    previous: list[int] = []
    for candidate in step.actions:
        if not isinstance(candidate, Measure):
            continue
        for atom_id, _ in candidate.results:
            if candidate is action and atom_id in atoms:
                for other in previous:
                    if (
                        other in atoms
                        and _distance(atoms[atom_id], atoms[other])
                        < device.readout.min_spacing_um - COORDINATE_TOLERANCE
                    ):
                        failures.append(
                            ConstraintResult(
                                "readout_spacing",
                                f"Readout atoms {other} and {atom_id} are too close.",
                            )
                        )
            previous.append(atom_id)
        if candidate is action:
            return


def _holder(atom: AtomState) -> str:
    return "aod" if isinstance(atom.holder, AODHolder) else "slm"


def _distance(left: AtomState, right: AtomState) -> float:
    return hypot(left.position.x - right.position.x, left.position.y - right.position.y)
