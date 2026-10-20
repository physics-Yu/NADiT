# Changelog

## Unreleased

### Added

- A Python 3.12 physical compiler package with static OpenQASM 2.0 and 3.0
  loading functions.
- A device-independent Physical DAG with operation records, quantum and
  classical wire dependencies, register metadata, and global phase.
- Same-source custom-gate expansion and explicit rejection of unsupported
  dynamic constructs and external includes. Text and file input accept only
  the standard `qelib1.inc` (OpenQASM 2.0) or `stdgates.inc` (OpenQASM 3.0).
- Module-local dependency locking, frontend and IR tests, formatting, linting,
  and type-checking configuration.
