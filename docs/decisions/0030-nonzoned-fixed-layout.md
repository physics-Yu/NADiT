# 0030: Non-zoned compilation with a fixed reference layout

Status: Implemented and verified, 2026-10-22.

## Contract

Each circuit/job selects its compiler strategy. `nonzoned` uses an explicitly
given SLM entry layout as its reference. Each parallel CZ layer gathers its
pairs in the shared array, executes one pulse, then returns every transported
atom to its original SLM trap. Local operations keep the reference layout.
Retained measurements use the existing batch-readout component and return to
their entry traps. The compiler remains the owner of DAG progress, conditional
execution, result identities, sampling, and feedback latency.

Device `pair_arrays` describe isolated double-trap interaction cells throughout
the shared region. Home atoms occupy these declared traps; a gate can bring
either operand to the free trap beside its partner. A pair already sharing a
cell requires no movement. The layout is supplied through existing
`entry.kind=explicit`; no initial-placement search or dynamic SA is performed.
The reference is an atom-to-trap mapping, not merely an unordered set of points.

## Planning and emission

Use ENOLA's dual movement candidates, longest-first ordering, and greedy
independent-set selection. Keep selected directions and batches in a small
layer plan. Device motion rules supply compatibility, line spacing, capacity,
and workspace admission. Pickup and destination occupancy are whole-batch
constraints; close a proposed batch over other pending movers when possible.

Expand each accepted batch with the existing strict LOAD/MOVE/STORE semantics.
After all batches arrive, pulse the complete layer and reverse the batches in
reverse order. Do not repartition the chosen movements during emission. Full
action/semantic validators remain outside the construction loop.

This is an ENOLA-inspired fixed-layout baseline, not a complete ENOLA
reproduction: compatibility uses actual micrometer coordinates and Device
limits, and transfer uses common strict phases rather than upstream row-wise
CodeGen. This can split batches that upstream staged transfer would combine.
The geometry and movement semantics remain those of NADiT's current Device.

The global pulse covers the declared shared region(s). Add Device
`cz.allow_spectators`, default false, to permit isolated illuminated atoms on
this hardware profile. Unexpected interactions remain illegal, even when
spectators are allowed. Exposure remains visible in pulse coverage. This flag
does not supply a calibrated spectator-noise model.

## Interfaces and modules

- `nonzoned/planning.py`: dual candidates, compatible batches, execution layout.
- `nonzoned/emission.py`: forward and inverse batch Atom IR.
- `strategies/nonzoned.py`: implements existing `CompilationStrategy`.
- `strategy_selection.py`: per-circuit factory for the new strategy and the
  existing Search Smarter stage configuration. Job loading, describe, and JSON
  Schema expose the same selection.

`strategy={"kind":"nonzoned"}` selects this baseline. Optional
`max_cz_gates` limits a parallel layer; omission uses catalogue capacity.
The Python strategy accepts the same Device catalogue and returns existing
AtomFragment values. Physical DAG and Atom IR formats are unchanged.

First support flexible AOD, SLM-SLM CZ, and retained AOD readout when measurement
is present. Unsupported jobs use the existing exit-code-4 admission path.
Rigid routing, dynamic placement, code deformation, and layout-changing
operations are not part of this baseline.

## Verification

Test same-pair repetition, changing partners, parallel and conflicting moves,
pickup closure, finite AOD capacity, exact layer returns, measurement identities
and both conditional outcomes, feedback delays, semantic rejection of extra
couplings, and final replay. Compare compatibility and greedy selection with
the pinned ENOLA functions on small inputs. Keep existing test assertions.
Provide an executable example job and validate its saved self-contained result.

The complete suite passes 1,650 tests against the 1,626-test baseline. The 24
new cases cover the contracts above, column capacity, no-move interactions,
per-circuit selection, CLI exit code 4, and self-contained result validation.
The upstream kernel comparison checks 3,240 vector pairs and three complete
greedy-batching cases without additional capacity/occupancy constraints.
It does not establish equivalence to upstream staged CodeGen. Existing test
expectations are preserved; zoned-only tests add strategy type narrowing.
Formatting, lint, and mypy pass over source, tests, and examples (236 files).

`examples/jobs/nonzoned.json` exercises eight atoms, two four-CZ layers, two
measurements, resets, and conditional operations. Seed 17 selects the rotation
branch and skips the conditional CZ; tests cover both values. The example has
28 steps, two four-pair pulses, one two-result readout, 18 atom loads and stores,
332 us transport, and 804 us total planned duration. Independent saved-result
validation checks motion, coverage, replay, pulses, addressing, and timing.
Artifacts are under `modules/physical_compiler/artifacts/nonzoned-fixed/`:
result JSON, Atom IR text, and an offline viewer exported from the saved result.
The viewer is exported; interactive visual acceptance is not recorded.

## Source

ENOLA, commit `2944dbf4e163e8d2eeeec607add0d9139edce689`:
[router_mis.py](https://github.com/UCLA-VAST/Enola/blob/2944dbf4e163e8d2eeeec607add0d9139edce689/enola/router/router_mis.py).
The comparison project supplies the fixed-home design reference, not its
candidate restrictions, per-batch pulse boundary, or runtime framework.
