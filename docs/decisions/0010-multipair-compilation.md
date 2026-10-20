# 0010: Multi-Pair Compilation

Status: Accepted on 2026-10-20. The correctness baseline is implemented;
transport optimization, reuse, and IDS are subsequent increments.

## Context

The one-pair pipeline connects native operations, placement, transport, and
Atom IR with compile-time measurement feedback. Multiple CZ pairs need a
shared pulse, coordinated site allocation, and transport among spectators.

## Module handoffs

Keep PhysicalDAG, GateLayer, PlacementPlan, AtomFragment, and the compiler's
ready-set ownership. The strategy composes the algorithm modules.

| Module | Multi-pair responsibility |
| --- | --- |
| Scheduling | Select disjoint ready CZ operations up to the fixed pair capacity |
| Reuse | Match shared atoms between known neighboring CZ layers |
| Placement | Allocate distinct gate pairs and choose execution/exit layouts |
| Transport | Group compatible movements and construct transfers and moves |
| Emission | Emit one CZ pulse covering all selected gates in the same region |

Reuse analysis will receive a known following layer. Strategy and placement
will receive that same optional lookahead. Preview uses temporary planning
progress, stops at unresolved measurement branches, and does not sample
results or change the compiler's committed ready set.

## Conflict map and placement cost

A transport conflict map belongs to one layout transition, AOD model, and
construction method. Nodes are atom movements with fixed source/destination
positions and holders. Edges describe pairwise incompatibility:

- Common-load rigid translation requires equal displacement vectors.
- Flexible strict transport preserves equality and order on both axes.
- QMAP relaxed routing preserves axis equality but can handle certain order
  changes through staged pickup, offsets, and release.

Each emitted MOVE still obeys the active line constraints. A more general
staged rigid or line-splitting method would need its own compatibility rules.

Batch construction also maintains line capacity, current occupancy, pickup
order, offset space, and destination-release dependencies. These properties
are updated incrementally; they are not all pairwise graph edges. Full
transport checks remain test/debug tools.

Placement and routing will share the same compatibility and timing rules.
Placement extends partial assignments by updating compatible groups and their
estimated cost. Cost uses configured transfer times and, for each movement
phase, the maximum atom distance times the movement-time coefficient.

IDS means Iterative Diving Search: follow the best child toward a complete
layout, keep alternatives in a bounded queue, and compare a configured number
of completed candidates. It does not generate atom actions during search.

## Implementation order

1. Correctness baseline: fixed pair catalogue, capacity-limited multi-CZ layers,
   distinct pair placement, sequential transport, and one pulse per layer.
2. Shared transport rules and strict parallel batching.
3. Following-layer preview and matching-based atom reuse.
4. IDS placement using the tested transport cost.
5. Relaxed routing and its complete staged transfer/offset construction.

Each increment preserves measurement identities, branch selection, action
coverage, dependency timing, and replayed exit state.

## First increment

Bind an explicit list of SLM site pairs to one CZ zone. Validate this catalogue
once: sites are distinct, each pair is within the interaction radius, and
different pairs cannot interact under the shared pulse. Its size sets the CZ
layer capacity.

The baseline placer assigns ready gates to unused catalogue pairs
deterministically. Every CZ layer returns its participating atoms to storage.
Spectators inside the pulse coverage are also assigned storage positions.
Thus changing partners between layers does not require in-place gate-site
swaps. Reuse and placement search are later increments.

Sequential transport uses one active row and one active column for one atom
at a time. It supports any number of SLM-held atoms, resolves vacant
destinations first, and retains the existing singleton AOD readout path.
Closed swaps requiring a temporary buffer remain outside this constructor.

Emit one CZPulse with the catalogue zone's coverage and all selected native
CZ node IDs. Measurement results are sampled by the existing compiler;
conditional gates and phases retain their current behavior.

The baseline uses the existing distance-to-time model and adds no dependencies.

## References

- [Search Smarter](https://arxiv.org/html/2512.13790v1): pipeline, IDS, and relaxed routing.
- [MQT reuse analysis](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/reuse_analyzer/VertexMatchingReuseAnalyzer.cpp):
  bipartite matching between neighboring CZ layers. Build all shared-endpoint
  candidate edges; the pinned version's else-if construction can omit one.
- [MQT routing compatibility](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp):
  strict and relaxed movement compatibility and transport grouping.
