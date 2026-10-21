# Changelog

## Unreleased

### Fixed

- Match source placement float accumulation boundaries and operation order,
  including candidate means, lookahead, group cost, and displacement deviation.
- Match the source IDS indexed min/max heaps when equal-priority alternatives
  are removed, replaced, and selected for another trial.
- Correct upstream initial-array row reset/end access, exhausted-window
  termination, nearest-column distance assignment, and straddled-y nearest-SLM
  arithmetic; record executable source differences separately from equal cases.
- Correct the source gate-column spreading boundary and use endpoint-specific
  pair separations instead of limiting column count or forcing direct fallback.
- Match source distance/candidate tie ordering and drain ready local rotations
  before CZ selection to expose independent gates in the same layer.
- Carry actual retained-atom snapshots between compilation layers instead of
  treating every occupied gate site as a reuse anchor.
- Clear nonretained pulse-region occupants before greedy or IDS gate placement,
  emitting the preparation transport and its duration. Local-control relocation
  can no longer create false cross-pair anchors.

### Changed

- Remove staged transport's two-column cap and Cartesian-mask completion.
  Cached source-template stages supply actual AOD capacity, spacing, workspace,
  and occupied intersections instead of broad geometry envelopes.
- Lookahead can use already compiled measurement results during planned
  feedback latency; actual conditional actions still wait for result availability.
- Reuse comparison reports both atom-load and atom-store counts alongside
  transfer batches and predicted transport time.
- QASM import now translates to a configurable native GateSet before building
  PhysicalDAG; the default basis is RX/RY/RZ/CZ.
- Measurement references use stable pre-translation source identities,
  independently of final native node IDs. Native children retain their source
  IDs and conditions, including conditional decomposition phases.
- Renamed the physical circuit module to `physical_ir.py` and its tests to
  `test_physical_ir.py`; package-level circuit exports remain available.
- Positioned full transport checks as test/debug reference tools, with legal
  construction as the default compiler route.
- Clarified the physical compiler route: compiler-owned DAG progress and
  planned state, with fake results sampled during measurement compilation.
  The existing event executor remains the DAG feedback reference tester.

### Added

- Declarative `nadit.job/1` loading and a per-stage strategy registry, with
  resolved paths/defaults, explicit or automatic entry layouts, and stable
  source-measurement overrides.
- Optional result validation with the first transport constraint, source-operation
  coverage, or replay mismatch, separate from normal compilation.
- Shared compilation metrics used by saved results, examples, and the candidate
  benchmark runner, preserving the existing example output.
- Versioned `nadit.result/1` JSON serialization for all Atom IR actions,
  layouts, result records, Device snapshots, and software provenance.
- Offline Atom IR animation with sparse replay patches, step/time seeking,
  AOD and SLM geometry, pulse coverage, measurement availability, and atom
  inspection. Includes two feedback outcomes and a compiled 60-atom example.
- Complete pinned C++ static-stage traces and first-divergence comparison,
  preserving original source failures separately from matching executions.
- Original capacity-based ASAP scheduling and endpoint-priority reuse policies,
  alongside the existing measurement-feedback and complete-edge policies.
- Multi-region gate catalogues, union CZ coverage, global RY and addressed U,
  source integer-unit staging offsets, and cross-zone transport batches.
- A* using shared placement candidates/scores, the paper's distance-to-time
  profile, and a saved-input 26-circuit benchmark and ablation runner.
- Source-layout SLM geometry, cached nearest-SLM/site queries, automatic initial
  storage placement, and configurable candidate windows with source expansion.
- Original gate-option score-distance metadata and full-free-array score ranks,
  with executed pinned C++ candidate and search-node comparisons.
- Optional relaxed routing with source-aligned compatibility, weighted conflicts,
  donor-group redistribution, and a configurable strict-fallback cost threshold.
- Device-admitted row/column reordering using the existing staged generator and
  explicit-grid Atom IR, with finite-capacity and occupancy constraints.
- Executed pinned C++ router and reversed-axis movement comparisons, plus
  measurement-feedback and per-action replay coverage.
- Staged strict transport with distance-ordered groups, row/column pickup,
  lattice offsets, combined transfers, and explicit AOD-grid Atom IR.
- Regular SLM staging geometry with optional explicit pitches and direct strict
  fallback for regions without the required space or supported geometry.
- A pinned upstream C++ rearrangement harness and small-case event comparison,
  plus staged feedback, per-action legality, and exit-replay coverage.
- Source-aligned routing-aware placement with full gate/storage candidates,
  compatible movement-group scoring, partner lookahead, and bounded IDS.
- Optional retention in the storage-placement search: matched atoms can stay
  at a gate site or return to storage according to the reference proxy score.
- An IDS placement mode and search configuration in the multi-pair study,
  sharing strict transport, measurement feedback, and action replay.
- Pure next-CZ preview using compiler progress and compiled results, stopping
  at unresolved feedback without sampling measurements.
- Maximum matching of shared CZ endpoints and reuse-aware placement that
  retains gate-site anchors across partner changes and addressed rotations.
- Optional `reuse_analyzer` strategy composition and `--reuse` study switch,
  with feedback, pulse geometry, strict transport, and terminal replay tests.
- Separate atom-load and transport-time metrics: reuse reduces loaded atoms
  in the current study but can increase strict batch count and duration.
- Shared strict movement rules, a pairwise conflict map, batch row/column
  capacity checks, stable endpoint frames, and a distance-based batch cost.
- Strict parallel transport with fixed-point source/target occupancy closure
  and direct common LOAD/MOVE/STORE actions.
- Sequential/strict comparison on the existing multi-pair example and
  independent tests for phase legality, capacity, ghosts, feedback, and replay.
- Multi-pair correctness baseline with explicit isolated SLM-pair catalogues,
  capacity-limited CZ layers, distinct pair assignments, spectator clearing,
  and storage returns between layers.
- Sequential singleton transport using an occupancy index and destination
  vacancy ordering, plus one shared CZ pulse for every placed gate layer.
- A multi-pair QASM example covering changed interaction partners and
  measurement-conditioned CZ layers, with geometry, timing, and replay tests.
- One-pair native-DAG-to-Atom-IR compilation, connecting ready-layer selection,
  fixed SLM pair placement, grouped/singleton AOD transport, and pulse emission.
- Compile-time measurement sampling with stable identities, conditional action
  selection, preserved phase, and explicit feedback waits.
- Device CZ, local-control, and readout capability records with optional JSON
  fields, plus direct atom-state replay for retained readout.
- An executable two-atom QASM study, explicit device configuration, and tests
  comparing constructed transport, replayed exit state, and selected branches.
- Separate parsing, native translation, and DAG adaptation modules, using the
  existing Qiskit dependency and a per-import gate-translation cache.
- Gate-layer, placement, and Atom IR fragment records with small scheduling,
  reuse, placement, and transport protocols.
- Immutable Atom IR actions, parallel steps, program timing, and Stim-style
  text output, with source-operation links and stable measurement references.
- A concrete atom program example covering transfer, movement, pulse,
  rotation, AOD-held readout, and feedback waiting.
- Flexible device geometry and JSON loading, explicit atom/holder and active
  AOD line state, and collective loading, movement, and unloading checks.
- A small zoned device fixture with 5 um storage spacing, paired gate sites,
  and a measurement region without SLM traps.
- Device and zoning design separating fixed SLM sites, interaction candidates,
  AOD motion, pulse coverage, and AOD-held measurement.
- A staged physical compiler implementation plan with file ownership and
  acceptance criteria, starting with the flexible zoned pipeline and then
  the rigid 16-by-16 variant.
- A Python 3.12 physical compiler package with OpenQASM 2.0 and 3.0 loading
  functions for static operations and single-bit measurement feedback.
- A device-independent Physical DAG with operation records, quantum and
  classical wire dependencies, register metadata, and global phase.
- Same-source custom-gate expansion and explicit rejection of unsupported
  control flow and external includes. Text and file input accept only
  the standard `qelib1.inc` (OpenQASM 2.0) or `stdgates.inc` (OpenQASM 3.0).
- Measurement-occurrence references and fixed single-bit conditional bodies,
  including `if` / `else`, with explicit result dependencies.
- Dependency execution traces with reproducible fake measurement results,
  explicit result overrides, and result latency separate from operation time.
- Module-local dependency locking, frontend and IR tests, formatting, linting,
  and type-checking configuration.
