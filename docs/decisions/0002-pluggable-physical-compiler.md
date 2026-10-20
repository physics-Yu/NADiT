# 0002: Pluggable Physical Compiler

Status: Direction accepted on 2026-10-20.

## Context
NADiT needs to compare compiler solutions across configurable neutral-atom devices.
The framework must support more than one complete compilation strategy.

## Decision
A pluggable compiler strategy receives a Physical DAG, Device capabilities,
configuration, entry state, and a measurement-result provider. It produces
the atom-action program for the selected case.
Device capabilities constrain planning, transport batching, action expansion, and
timing. Every intermediate move must obey the selected AOD model, including rigid translation.
First dissect [Search Smarter, Not Harder](https://arxiv.org/abs/2512.13790),
then establish the common framework, then implement and compare several strategies.
Complete the physical compiler core before implementing reusable component packaging.

## Alternatives
A single fixed compiler would require implementation changes for each
strategy comparison.

## Consequences
Device capability selection and compiler strategy selection are distinct choices.
Strategies share the executable atom-action contract and honor their supported Device capabilities.
The compiler-owned planning loop is defined in
[ADR 0005](0005-case-driven-physical-compilation.md).
Concrete Device and Atom IR interfaces and the first strategy are the next
implementation work.
