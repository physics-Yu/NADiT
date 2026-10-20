# 0001: Module Workspaces

Status: Accepted on 2026-10-20.

## Context
Physical compiler work needs a defined location while its internals remain open.

## Decision
Use `modules/physical_compiler/` within the existing NADiT Git repository.
Keep global architecture and rules at the root, and shared ADRs in `docs/decisions/`.
Use `src/`, `tests/`, `docs/`, and `configs/` within the physical compiler workspace
for implementation, tests, module notes, and example device configurations.
Keep a module `README.md` as its entry point and track empty folders with `.gitkeep`.

## Alternatives
A repository-wide source/test split was considered; the accepted layout keeps
each module's work together without selecting an implementation language.

## Consequences
Module-specific work stays together; the module chat determines the organization
inside the accepted folders, along with the language, packaging, and interfaces.
Add other module workspaces when needed. All work follows the root `agent.md`.
