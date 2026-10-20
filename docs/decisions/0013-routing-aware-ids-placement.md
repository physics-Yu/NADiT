# 0013: Routing-Aware IDS Placement

Status: Implemented on 2026-10-20.

## Context

The existing multi-pair baseline greedily assigns gate pairs and retains every
matched reuse candidate. Strict transport can split those assignments into
more batches. Search Smarter's public implementation jointly considers
compatible movement groups, future partners, and optional retention during
placement search.

Reference: MQT QMAP commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`,
`HeuristicPlacer.cpp` and `HeuristicPlacer.hpp`.

## Decision

Add a separate `HeuristicPlacer`; keep `MultiPairPlacer` as the comparison
baseline. Preserve PhysicalDAG, PlacementPlan, AtomFragment, and Atom IR.
Add an optional `following` argument to placement so candidates can use the
known next CZ partners. Existing placers accept it without changing behavior.

The implementation separates:

| Module | Responsibility |
| --- | --- |
| `placement/candidates.py` | Gate-pair and storage options, job ordering, partner lookahead |
| `placement/scoring.py` | Search records, first-fit compatibility groups, cost and heuristic |
| `placement/ids.py` | Bounded iterative diving search |
| `placement/heuristic.py` | Gate placement, exit placement, and state handoff |

First search the execution gate assignments, then search storage/retention
assignments after the pulse. Existing retained participants anchor their gate
pairs; reserve those pairs before assigning other gates. Clear pulse-region
spectators. Local operations and readout keep the existing placement helper.

## Candidates

Use the reference's full-candidate mode (`useWindow=false`) over the configured
fixed gate catalogue and reachable storage SLM sites. A gate candidate chooses
the orientation with the smaller sum of movement distances for that pair.
Order gate jobs by descending nearest-pair maximum distance. For exit placement,
start with the atom farthest from storage, then order the remaining atoms by
distance from that atom.

For the finite Device catalogue, nearest gate pairs minimize total distance
among available pairs; nearest storage sites and column span come from the
available storage options. The reference instead has architecture-specific
precomputed nearest-grid-site lookups. This adaptation changes job order and
heuristic values when those geometric nearest sites are unavailable.

Each matched reuse candidate receives both a stay option and storage options.
Stay keeps its gate site and adds no movement to the exit groups. Actual
retention is determined by the selected exit layout, not by the matching set.
The strategy carries actual selected stays as explicit atom-state snapshots,
as specified in [ADR 0014](0014-explicit-retained-atoms.md). The next placement
anchors only unchanged retained participants.

## Score

Use the public implementation's proxy score:

```text
g = sum(sqrt(maximum Euclidean distance in each compatibility group))
    + accumulated partner-lookahead cost

h = max(0, sqrt(maximum first-available-option unplaced distance)
           - sqrt(maximum already placed distance))
    + sum(mean candidate lookahead for each remaining job)
    + deepening_factor * (group displacement deviation + deepening_value)
      * remaining_jobs
```

Extend groups in first-fit order. Reuse the Device-backed strict axis
compatibility, spacing, and row/column capacity rules. Group deviation uses
discrete source/target axis ranks and scale factors. Occupancy closure and
actual transfer phases remain the final strict transporter's responsibility.
Axis ranks use the same coordinate tolerance as the transport rules.
The remaining-distance term scans the first available option in the reference
ordering, including its lexicographic gate-distance ordering.

Gate lookahead considers a selected reusable atom's next partner. Storage
lookahead uses the next partner's position in the execution layout:

```text
storage option: lookahead_factor * sqrt(partner distance)
stay option: max(0, sqrt(partner distance) - reuse_level)
```

Use reference defaults: `deepening_factor=0.01`, `deepening_value=0`,
`lookahead_factor=0.4`, `reuse_level=5`, `trials=4`, `queue_capacity=100`.
These scores are search proxies. Device transfer times and its distance-to-time
model determine the generated program's reported microseconds.

IDS dives directly into the lowest `g+h` child, queues alternatives with bounded
capacity, restarts after a goal or dead end, and returns the best completed
candidate found within the configured trial budget.

## Consequences

Search does not generate Atom IR, invoke transport for each candidate, sample
measurements, or evaluate a complete two-layer transport schedule.
The compiler continues to own progress and compiled results; actual conditional
actions still obey feedback latency. Final transport and emission use the
existing interfaces.

## Verification

- Candidate orientations, occupied sites, job ordering, and partner estimates.
- First-fit groups, capacity adaptation, score equations, and bounded IDS.
- Optional reuse retained or declined by score.
- Gate capacity, partner changes, feedback, exact pulse interactions, every
  emitted movement phase, and exit-state replay.
- Same-circuit, same-device, same-result comparisons against the greedy
  no-reuse and fixed-matching-reuse baselines, reporting atom loads/stores,
  transfer batches, and transport time separately.

## References

- [Search Smarter](https://arxiv.org/html/2512.13790v1), sections 3.1, 3.2 and 4.1.
- [Placement and scoring source](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/layout_synthesizer/placer/HeuristicPlacer.cpp).
- [IDS source and configuration](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/include/na/zoned/layout_synthesizer/placer/HeuristicPlacer.hpp).
