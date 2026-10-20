# 0002: Pluggable Physical Compiler

Status: Direction accepted on 2026-10-20; interfaces and implementation remain open.

## Context
NADiT needs to compare compiler solutions across configurable neutral-atom devices.
The framework must support more than one complete compilation strategy.

## Decision
A pluggable compiler strategy receives a physical circuit/DAG, Device capabilities,
and entry state, and completes the pipeline to executable atom actions.
Device capabilities constrain planning, transport batching, action expansion, and
timing. Every intermediate move must obey the selected AOD model, including rigid translation.
First dissect [Search Smarter, Not Harder](https://arxiv.org/abs/2512.13790),
then establish the common framework, then implement and compare several strategies.
Complete the physical compiler core before implementing reusable component packaging.

## Alternatives
A single fixed compiler does not meet the requested ability to replace complete solutions.

## Consequences
Device capability selection and compiler strategy selection are distinct choices.
Strategies share the executable atom-action contract and honor their supported Device capabilities.
Exact interfaces, internal stages, optional stage-level plugins, initial strategy
variants, supported capability combinations, and comparison metrics remain open.
This record authorizes documentation, not implementation; the reference is not a selected default.
