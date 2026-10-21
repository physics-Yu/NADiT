"""Nearest-SLM gate and storage jobs adapted from MQT QMAP.

Reference: MQT QMAP cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d,
HeuristicPlacer.cpp (MIT); see the repository third-party notice.
"""

# Copyright (c) 2023-2025 Chair for Design Automation, TUM.
# Copyright (c) 2025 Munich Quantum Software Company GmbH.
# See docs/third-party-notices.md for the source revision and MIT license.

from collections.abc import Mapping
from math import hypot, inf, sqrt

from ..device import Position, SLMSite
from ..state import AtomState, SLMHolder
from ..transport.rules import MoveRequest
from .arithmetic import f32 as _float
from .geometry import PlacementGeometry, SLMArray
from .scoring import PlacementConfig, PlacementJob, PlacementOption
from .windows import candidate_indices

_DEFAULT_CONFIG = PlacementConfig()


def gate_jobs(
    gates: tuple[tuple[AtomState, AtomState], ...],
    pairs: tuple[tuple[SLMSite, SLMSite], ...],
    *,
    reuse_atoms: frozenset[int] = frozenset(),
    partner_positions: Mapping[int, Position] | None = None,
    config: PlacementConfig = _DEFAULT_CONFIG,
    geometry: PlacementGeometry | None = None,
    qubit_ids: Mapping[int, int] | None = None,
) -> tuple[PlacementJob, ...]:
    """Build one nearest orientation per pair and order the gate jobs.

    Args:
        gates: Atoms of unanchored gates in layer order.
        pairs: Available, reachable catalogue pairs.
        reuse_atoms: Matched current atom IDs eligible for later retention.
        partner_positions: Known next partners at their entry positions.
        config: Lookahead factor used in candidate costs.
        geometry: Regular SLM geometry for the original nearest/window path.
            Omit for the generic full-catalogue path.
        qubit_ids: Atom-to-qubit identities used to break equal job priorities.

    Returns:
        Jobs ordered by descending nearest-pair maximum distance and then by
        descending atom pair, matching the reference scheduler's canonical
        pairs. Options are ordered lexicographically by their two movement
        distances.

    Raises:
        ValueError: If a gate has no available pair.
    """
    if geometry is not None:
        return _source_gate_jobs(
            gates,
            pairs,
            reuse_atoms,
            partner_positions or {},
            config,
            geometry,
            qubit_ids or {},
        )
    partners = partner_positions or {}
    identities = qubit_ids or {}
    jobs: list[tuple[float, tuple[int, int], PlacementJob]] = []
    for first, second in gates:
        options = tuple(
            _gate_option(first, second, pair, reuse_atoms, partners, config)
            for pair in pairs
        )
        if not options:
            raise ValueError("A gate has no available catalogue pair.")
        nearest = min(options, key=lambda option: sum(option.distances))
        ordered = tuple(sorted(options, key=lambda option: option.distances))
        first_id = identities.get(first.id, first.id)
        second_id = identities.get(second.id, second.id)
        atom_pair = (min(first_id, second_id), max(first_id, second_id))
        jobs.append((nearest.distance, atom_pair, PlacementJob(ordered)))
    return tuple(
        job for _, _, job in sorted(jobs, key=lambda item: item[:2], reverse=True)
    )


def storage_jobs(
    atoms: tuple[AtomState, ...],
    sites: tuple[SLMSite, ...],
    *,
    reuse_atoms: frozenset[int] = frozenset(),
    partner_positions: Mapping[int, Position] | None = None,
    config: PlacementConfig = _DEFAULT_CONFIG,
    geometry: PlacementGeometry | None = None,
    qubit_ids: Mapping[int, int] | None = None,
) -> tuple[PlacementJob, ...]:
    """Build storage options and optional zero-movement gate-site stays.

    Args:
        atoms: Current gate participants in layer order.
        sites: Reachable storage sites unoccupied by stationary atoms.
        reuse_atoms: Matched participants permitted to stay at their gate sites.
        partner_positions: Known next partners in the execution layout.
        config: Lookahead and reuse preference parameters.
        geometry: Regular SLM geometry for the original nearest/window path.
            Omit for the generic full-catalogue path.
        qubit_ids: Atom-to-qubit identities used to break equal job priorities.

    Returns:
        Jobs beginning with the atom farthest from its nearest storage site,
        followed by increasing distance from that first atom and then atom ID.

    Raises:
        ValueError: If an atom has neither a storage nor a stay option.
    """
    if not atoms:
        return ()
    if not sites:
        return tuple(
            _storage_job(atom, (), reuse_atoms, partner_positions or {}, config)
            for atom in atoms
        )
    if geometry is not None:
        return _source_storage_jobs(
            atoms,
            sites,
            reuse_atoms,
            partner_positions or {},
            config,
            geometry,
            qubit_ids or {},
        )
    partners = partner_positions or {}
    identities = qubit_ids or {}
    first_index = max(
        range(len(atoms)),
        key=lambda index: min(
            (_distance(atoms[index].position, site.position) for site in sites),
            default=inf,
        ),
    )
    first = atoms[first_index]
    remaining = sorted(
        (atom for index, atom in enumerate(atoms) if index != first_index),
        key=lambda atom: (
            _distance(atom.position, first.position),
            identities.get(atom.id, atom.id),
        ),
    )
    return tuple(
        _storage_job(atom, sites, reuse_atoms, partners, config)
        for atom in (first, *remaining)
    )


def _gate_option(
    first: AtomState,
    second: AtomState,
    pair: tuple[SLMSite, SLMSite],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
) -> PlacementOption:
    oriented = min(
        (pair, pair[::-1]),
        key=lambda sites: (
            _distance(first.position, sites[0].position)
            + _distance(second.position, sites[1].position)
        ),
    )
    assignments = ((first.id, oriented[0]), (second.id, oriented[1]))
    cost = 0.0
    for atom_id, site in assignments:
        if atom_id in reuse_atoms and atom_id in partners:
            cost = config.lookahead_factor * sqrt(
                _distance(site.position, partners[atom_id])
            )
            break
    return PlacementOption(
        assignments,
        (
            MoveRequest(first.id, first.position, oriented[0].position),
            MoveRequest(second.id, second.position, oriented[1].position),
        ),
        lookahead_cost=cost,
    )


def _storage_job(
    atom: AtomState,
    sites: tuple[SLMSite, ...],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
) -> PlacementJob:
    options: list[PlacementOption] = []
    partner = partners.get(atom.id)
    if atom.id in reuse_atoms:
        assert isinstance(atom.holder, SLMHolder)
        cost = (
            max(0.0, sqrt(_distance(atom.position, partner)) - config.reuse_level)
            if partner is not None
            else 0.0
        )
        options.append(
            PlacementOption(
                ((atom.id, SLMSite(atom.holder.site_id, atom.position)),),
                (),
                lookahead_cost=cost,
            )
        )
    for site in sites:
        cost = (
            config.lookahead_factor * sqrt(_distance(site.position, partner))
            if partner is not None
            else 0.0
        )
        options.append(
            PlacementOption(
                ((atom.id, site),),
                (MoveRequest(atom.id, atom.position, site.position),),
                lookahead_cost=cost,
            )
        )
    if not options:
        raise ValueError(f"Atom {atom.id} has no storage or retention candidate.")
    return PlacementJob(tuple(sorted(options, key=lambda option: option.distance)))


def _distance(first: Position, second: Position) -> float:
    return hypot(first.x - second.x, first.y - second.y)


def _source_gate_jobs(
    gates: tuple[tuple[AtomState, AtomState], ...],
    pairs: tuple[tuple[SLMSite, SLMSite], ...],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
    geometry: PlacementGeometry,
    qubit_ids: Mapping[int, int],
) -> tuple[PlacementJob, ...]:
    allowed = {frozenset((first.id, second.id)) for first, second in pairs}
    available = {
        array.id: frozenset(
            (row, column)
            for row in range(array.n_rows)
            for column in range(array.n_cols)
            if frozenset(site.id for site in array.gate_pair(row, column)) in allowed
        )
        for pair in geometry.entanglement_arrays
        for array in pair
    }
    jobs: list[tuple[float, tuple[int, int], PlacementJob]] = []
    for first, second in gates:
        # Source gate pairs are canonical qubit pairs, independently of the
        # atom identifiers chosen by the caller.
        if qubit_ids.get(first.id, first.id) > qubit_ids.get(second.id, second.id):
            first, second = second, first
        array, row, column = geometry.nearest_entanglement(
            first.position, second.position
        )
        nearest_pair = array.gate_pair(row, column)
        direct = (
            _distance(first.position, nearest_pair[0].position),
            _distance(second.position, nearest_pair[1].position),
        )
        reverse = (
            _distance(second.position, nearest_pair[0].position),
            _distance(first.position, nearest_pair[1].position),
        )
        priority = max(direct if sum(direct) <= sum(reverse) else reverse)
        indices = candidate_indices(
            array.n_rows,
            array.n_cols,
            (row, column),
            available[array.id],
            len(gates),
            config,
            stage="gate",
        )
        options = tuple(
            _source_gate_option(
                first,
                second,
                array.gate_pair(candidate_row, candidate_column),
                reuse_atoms,
                partners,
                config,
            )
            for candidate_row, candidate_column in indices
        )
        if not options:
            raise ValueError("A gate has no available pair in its nearest SLM.")
        ordered = tuple(sorted(options, key=lambda option: option.distances))
        job_key = (
            qubit_ids.get(first.id, first.id),
            qubit_ids.get(second.id, second.id),
        )
        jobs.append((priority, job_key, PlacementJob(ordered)))
    return tuple(
        job for _, _, job in sorted(jobs, key=lambda item: item[:2], reverse=True)
    )


def _source_gate_option(
    first: AtomState,
    second: AtomState,
    pair: tuple[SLMSite, SLMSite],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
) -> PlacementOption:
    dis1 = _float(_distance(first.position, pair[0].position))
    dis2 = _float(_distance(second.position, pair[0].position))
    dis3 = _float(_distance(first.position, pair[1].position))
    dis4 = _float(_distance(second.position, pair[1].position))
    if _float(dis1 + dis4) <= _float(dis2 + dis3):
        oriented, score_distances = pair, (dis1, dis4)
    else:
        # Preserve the source proxy's reversed distance tuple separately from
        # the real first-to-other and second-to-nearest movement assignments.
        oriented, score_distances = pair[::-1], (dis2, dis3)
    assignments = ((first.id, oriented[0]), (second.id, oriented[1]))
    cost = 0.0
    for atom_id, site in assignments:
        if atom_id in reuse_atoms and atom_id in partners:
            cost = _lookahead(site.position, partners[atom_id], config.lookahead_factor)
            break
    return PlacementOption(
        assignments,
        (
            MoveRequest(first.id, first.position, oriented[0].position),
            MoveRequest(second.id, second.position, oriented[1].position),
        ),
        lookahead_cost=cost,
        score_distances=score_distances,
    )


def _source_storage_jobs(
    atoms: tuple[AtomState, ...],
    sites: tuple[SLMSite, ...],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
    geometry: PlacementGeometry,
    qubit_ids: Mapping[int, int],
) -> tuple[PlacementJob, ...]:
    nearest = {atom.id: geometry.nearest_storage(atom.position) for atom in atoms}
    first_index = max(
        range(len(atoms)),
        key=lambda index: _distance(
            atoms[index].position,
            nearest[atoms[index].id][0]
            .site(nearest[atoms[index].id][1], nearest[atoms[index].id][2])
            .position,
        ),
    )
    first = atoms[first_index]
    remaining = sorted(
        (atom for index, atom in enumerate(atoms) if index != first_index),
        key=lambda atom: (
            _distance(atom.position, first.position),
            qubit_ids.get(atom.id, atom.id),
        ),
    )
    allowed = {site.id for site in sites}
    available = {
        array.id: frozenset(
            (row, column)
            for row in range(array.n_rows)
            for column in range(array.n_cols)
            if array.site(row, column).id in allowed
        )
        for array in geometry.storage_arrays
    }
    jobs = []
    for atom in (first, *remaining):
        array, row, column = nearest[atom.id]
        indices = candidate_indices(
            array.n_rows,
            array.n_cols,
            (row, column),
            available[array.id],
            len(atoms),
            config,
            stage="storage",
            initial_options=int(atom.id in reuse_atoms),
        )
        jobs.append(
            _source_storage_job(
                atom, array, indices, (row, column), reuse_atoms, partners, config
            )
        )
    return tuple(jobs)


def _source_storage_job(
    atom: AtomState,
    array: SLMArray,
    indices: tuple[tuple[int, int], ...],
    nearest: tuple[int, int],
    reuse_atoms: frozenset[int],
    partners: Mapping[int, Position],
    config: PlacementConfig,
) -> PlacementJob:
    options: list[PlacementOption] = []
    partner = partners.get(atom.id)
    if atom.id in reuse_atoms:
        assert isinstance(atom.holder, SLMHolder)
        cost = (
            max(
                0.0,
                _float(sqrt(_float(_distance(atom.position, partner))))
                - _float(config.reuse_level),
            )
            if partner is not None
            else 0.0
        )
        options.append(
            PlacementOption(
                ((atom.id, SLMSite(atom.holder.site_id, atom.position)),),
                (),
                lookahead_cost=_float(cost),
            )
        )
    for row, column in indices:
        site = array.site(row, column)
        options.append(
            PlacementOption(
                ((atom.id, site),),
                (MoveRequest(atom.id, atom.position, site.position),),
                lookahead_cost=(
                    _lookahead(site.position, partner, config.lookahead_factor)
                    if partner is not None
                    else 0.0
                ),
                score_distances=(_float(_distance(atom.position, site.position)),),
            )
        )
    if not options:
        raise ValueError(f"Atom {atom.id} has no option in its nearest storage SLM.")
    return PlacementJob(
        tuple(sorted(options, key=lambda option: option.distance)),
        nearest_site=array.site(*nearest),
    )


def _lookahead(first: Position, second: Position, factor: float) -> float:
    return _float(_float(factor) * _float(sqrt(_float(_distance(first, second)))))
