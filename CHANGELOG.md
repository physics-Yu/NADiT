# Changelog

## Unreleased

### Changed

- Clarified the physical compiler route: compiler-owned DAG progress and
  planned state, with fake results sampled during measurement compilation.
  The existing event executor remains the DAG feedback reference tester.

### Added

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
