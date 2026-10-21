# Device Geometry and Capabilities

Date: 2026-10-21.
Status: Flexible geometry, atom state, operation capabilities, semantic
validation, reuse, layout search, and direct/staged/relaxed routing are
implemented. Rigid motion, shared placement/transport rules, and the three
comparison profiles are implemented under
[ADR 0024](../../../docs/decisions/0024-rigid-aod-motion.md).

## Device and compiler responsibilities

Readout `max_atoms` is the per-channel concurrent exposure capacity: 1 by
default, a positive integer for a finite capacity, or null for no count limit.
Ready scheduling and the flexible batch-readout planner use this value.
The batch remains in AOD traps for measurement and returns to its original
SLM sites. Result identity and feedback latency do not depend on batch size.
The semantic validator checks the total simultaneous measurement count,
including multiple Measure actions on the same channel.

Device `pair_arrays` own the interaction catalogue. Left/right grid cells are
paired, or reference matrices identify existing sites. Loading validates
pair isolation; result snapshots store references without duplicating sites.
Optional zone roles select storage defaults independently of zone names.
`architectures.generate("zoned/v1", params)` expands parameterized blocks
and checks overlap, workspace coverage, and pair geometry before compilation.
See [ADR 0025](../../../docs/decisions/0025-parametric-architectures-and-strategy-support.md).

Device describes available geometry, traps, controls, and operation times.
The compiler chooses atom assignments, interaction positions, transport
batches, transfers, and pulse coverage within those capabilities.

| Object | Device description | Compiler decision |
| --- | --- | --- |
| Zone | Bounds, allowed operations, control resources | Which atoms enter, remain, or leave |
| SLM sites | Fixed coordinates, generated from a grid or listed explicitly | Atom occupancy |
| AOD | Workspace, row/column capacity, minimum line spacing, motion model | Active lines, carried atoms, legal grouped moves |
| CZ capability | Illumination control, interaction distances, allowed holder pairs | Interaction geometry and each pulse's coverage |
| Measurement capability | Region, allowed holders, addressing and spacing rules, duration, post-measurement retention | Readout placement and batches |
| Timing | Transfer, pulse, reset, readout, feedback, and distance-to-time parameters | Action start times and step durations |

Positions use micrometers and durations use microseconds. Zones can overlap;
shared storage and entanglement regions represent a non-zoned configuration.
Zone membership follows coordinates. Holder state identifies the SLM site or
AOD and its controlling row and column.

## Interaction positions

An interaction pair consists of two positions where a CZ can be performed.
It is distinct from a single SLM trapping site.

The multi-pair baseline binds an explicit list of SLM site pairs to one CZ
zone with `GatePairCatalog.from_device`. The catalogue validates pair geometry
once and supplies both layer capacity and shared pulse coverage. Placement
clears other occupants from that coverage before emitting the pulse.

The first placement strategy uses a finite set of fixed candidate pairs and
assigns atoms to them dynamically for each gate layer. Configuration can
generate these pairs from an origin, pair count, internal separation, and
pair pitch. Placement optimizes reuse, transport compatibility, and distance.

Fixed candidates are a strategy choice. Device legality also supports
strategies that generate candidate coordinates from the current state:

- SLM-SLM requires an existing SLM site at each endpoint.
- AOD-SLM requires an SLM site at the stationary endpoint and a reachable AOD
  position at the other endpoint.
- AOD-AOD requires both positions to satisfy the active AOD control constraints.

The allowed holder pairs are configurable; the target design supports all
three. A CZ pulse preserves holder state. AOD atoms can remain in AOD at a
candidate position when that trap configuration supports the pulse.

Interaction distance and illumination extent are separate parameters.
The compiler checks the occupied positions covered by each pulse, including
spectators, to avoid unintended interactions. Individually legal candidate
pairs do not establish joint pulse compatibility.

In the initial threshold model, an illuminated pair at distance `<= R`
counts as an interaction. The resulting pairs must match the intended gates;
cross-pair distances must exceed `R`.

## Initial geometry

| Region or capability | Configuration |
| --- | --- |
| Storage | SLM minimum spacing 5 um; initial grid uses 5 um pitch, with configurable origin and row/column counts |
| Entanglement | One occupied gate row within configured bounds; other atom rows more than 100 um away during the pulse |
| Gate spacing | SLM minimum spacing 2 um in the gate region; pair separation is an explicit configuration value |
| Interaction model | Initial distance threshold approximately 4.6 um |
| Measurement | Configured readout region, with AOD included among allowed holders |

The 5 um storage grid and denser gate positions are simultaneous parts of
the SLM pattern. Moving between them changes atom positions and occupancy.
SLM spacing does not determine the AOD's minimum row or column spacing.

For a one-dimensional example, pairs at `(0, 2)`, `(8, 10)`, and `(16, 18)`
have 2 um internal spacing and 6 um nearest cross-pair spacing. Under the
initial threshold model, this separates the intended pairs. The 8 um pair
pitch is an illustrative value to replace with the selected device setting.

For `K` pairs, internal separation `d`, and pair pitch `P`, the occupied row
spans `(K - 1) * P + d`. The zone must contain that span and the configured
margins. Candidate count, pulse coverage, and joint compatibility determine
gate capacity. AOD row/column capacity determines transport capacity.

Storage placement can choose any legal vacant site; returning to an atom's
original site is a strategy option.

## Measurement in AOD

An AOD-held atom can move to a legal readout position and be measured there.
No SLM site or STORE action is required for this path. The atom remains
stationary during readout; shared AOD controls constrain simultaneous motion
of other carried atoms.

Measurement retention is explicit. A retained atom keeps its position and
holder; a removed atom releases its occupancy. A retained atom can proceed
to later operations, with reset represented by its own action when required.
Readout addressing, minimum spacing, and shared controls determine which
atoms can be measured together.

Measurement completion and feedback latency constrain later action times.
The compiler records the fake result once when compiling the source
measurement, as specified by [ADR 0005](../../../docs/decisions/0005-case-driven-physical-compilation.md).

## Motion and transfers

The planner tracks holders and controlling lines while choosing movement.
It accounts for transfer feasibility and time before emitting LOAD or STORE.
All atoms affected by active AOD intersections participate in the state update.

Rigid motion applies a common translation to the active grid. Order-preserving
motion permits line separation changes while preserving shared lines, line
order, minimum separation, and valid pickup/drop-off behavior. Every
intermediate movement follows the selected model.

`AOD.motion` selects `flexible` (the default) or `rigid`. In rigid mode all
active rows share one vertical displacement and all active columns share one
horizontal displacement, including unoccupied lines. A carrying rigid AOD
cannot add lines during LOAD. STORE can deactivate lines. The rigid and
flexible profiles share transfer times and the distance-to-time function.

Placement scoring and transport use `TransportRules` selected by `rules_for`.
`RigidRules` batches only equal-displacement requests; `StrictRules` retains
the flexible relation. Pair, sequential, and direct strict transport support
rigid motion. Staged and relaxed transport reject it explicitly.

Movement time is computed from distance. Transfer and native-operation times
are configured separately. Code generation expands a planned transport batch
into the required transfer and movement steps.

## Profiles and implementation order

| Profile | AOD model | Capacity | Purpose |
| --- | --- | --- | --- |
| `zoned_flexible` | Order-preserving rows/columns | No row/column count limit | First complete Search Smarter-based pipeline |
| `zoned_flexible_16` | Order-preserving rows/columns | Up to 16 rows and 16 columns | Isolate capacity effects |
| `zoned_rigid_16` | Rigid translation | Up to 16 rows and 16 columns | Experimental-device comparison |

All three profiles use the geometry above. Unlimited capacity removes only the
row/column count bound; intersection coupling and motion constraints remain.
The flexible 16-by-16 profile separates capacity effects from motion-model
effects in comparisons. The comparison files specify a 4-by-32 storage grid,
20 gate pairs at y=140 um, and measurement starting at y=260 um.

The first reference strategy can use SLM-held gate configurations and
SLM-to-SLM transport boundaries. Subsequent planning can use AOD-held gates
to reduce transfers. Measurement in AOD is part of the target action contract.

The remaining device values are zone bounds and positions, storage dimensions,
gate pair separation and pitch, AOD workspace and line spacing, illumination
limits, readout conditions and retention, and operation timing. Study fixtures
will supply explicit values for these fields.

`configs/one_pair_flexible.json` supplies those values for the initial
two-atom study. `CZCapability`, `LocalControl`, and `ReadoutCapability` bind
channels, timing, and permitted holders. The initial placer uses SLM-SLM CZ,
performs retained readout in AOD, and returns the read atom to its previous
SLM site. Readout spacing in this profile separates the measured atom from
the other atom. General readout grouping is later work.

## References

- [Search Smarter, Not Harder](https://arxiv.org/html/2512.13790v1):
  routing-aware placement, reuse, and transport batching.
- [MQT QMAP architecture at v3.5.0](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/include/na/zoned/Architecture.hpp):
  fixed paired SLM arrays and separate Rydberg illumination bounds.

The initial geometry adapts this reference flow to the single-row device
configuration discussed here.
