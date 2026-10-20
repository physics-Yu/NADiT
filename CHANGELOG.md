# Changelog

## Unreleased

### Changed

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
