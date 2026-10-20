# 0007: Physical IR and Atom IR

Status: Implemented on 2026-10-20.

## Context

The physical circuit and the atom-action program need distinct module names
and contracts. Atom actions are the selected output of compilation.

## Decision

Rename `ir.py` to `physical_ir.py`, retaining the Physical DAG records and
package-level exports. Add `atom_ir.py` with immutable actions, parallel
`AtomStep` records, and sequential `AtomProgram` records.

Each action carries duration and physical source IDs. Transport actions carry
target AOD frames and atom bindings. CZ actions carry pulse coverage;
measurements bind atom IDs to stable source result references.

Construction trusts the producer's parallelism and geometry decisions.
Transport reference checks remain available for tests and explicit debugging.
Scalar timing and angle checks keep the representation numerically defined.

## Alternatives

A shared `ir.py` would obscure the boundary between circuit dependencies and
concrete atom actions. Full Device validation during every action construction
would repeat work performed by legal transport construction.

## Consequences

The implemented text writer serializes an existing AtomProgram. Physical DAG
compilation, action replay, and device command translation consume these
contracts in later steps. See the
[Atom IR specification](../../modules/physical_compiler/docs/atom-ir-and-components.md).
