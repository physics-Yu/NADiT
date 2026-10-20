"""Full-candidate gate and storage jobs adapted from MQT QMAP.

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
from .scoring import PlacementConfig, PlacementJob, PlacementOption

_DEFAULT_CONFIG = PlacementConfig()


def gate_jobs(
    gates: tuple[tuple[AtomState, AtomState], ...],
    pairs: tuple[tuple[SLMSite, SLMSite], ...],
    *,
    reuse_atoms: frozenset[int] = frozenset(),
    partner_positions: Mapping[int, Position] | None = None,
    config: PlacementConfig = _DEFAULT_CONFIG,
) -> tuple[PlacementJob, ...]:
    """Build one nearest orientation per pair and order the gate jobs.

    Args:
        gates: Atoms of unanchored gates in layer order.
        pairs: Available, reachable catalogue pairs.
        reuse_atoms: Matched current atom IDs eligible for later retention.
        partner_positions: Known next partners at their entry positions.
        config: Lookahead factor used in candidate costs.

    Returns:
        Jobs ordered by descending nearest-pair maximum distance. Options are
        ordered lexicographically by their two movement distances.

    Raises:
        ValueError: If a gate has no available pair.
    """
    partners = partner_positions or {}
    jobs: list[tuple[float, PlacementJob]] = []
    for first, second in gates:
        options = tuple(
            _gate_option(first, second, pair, reuse_atoms, partners, config)
            for pair in pairs
        )
        if not options:
            raise ValueError("A gate has no available catalogue pair.")
        nearest = min(options, key=lambda option: sum(option.distances))
        ordered = tuple(sorted(options, key=lambda option: option.distances))
        jobs.append((nearest.distance, PlacementJob(ordered)))
    return tuple(job for _, job in sorted(jobs, key=lambda item: -item[0]))


def storage_jobs(
    atoms: tuple[AtomState, ...],
    sites: tuple[SLMSite, ...],
    *,
    reuse_atoms: frozenset[int] = frozenset(),
    partner_positions: Mapping[int, Position] | None = None,
    config: PlacementConfig = _DEFAULT_CONFIG,
) -> tuple[PlacementJob, ...]:
    """Build storage options and optional zero-movement gate-site stays.

    Args:
        atoms: Current gate participants in layer order.
        sites: Reachable storage sites unoccupied by stationary atoms.
        reuse_atoms: Matched participants permitted to stay at their gate sites.
        partner_positions: Known next partners in the execution layout.
        config: Lookahead and reuse preference parameters.

    Returns:
        Jobs beginning with the atom farthest from its nearest storage site,
        followed by increasing distance from that first atom.

    Raises:
        ValueError: If an atom has neither a storage nor a stay option.
    """
    if not atoms:
        return ()
    partners = partner_positions or {}
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
        key=lambda atom: _distance(atom.position, first.position),
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
