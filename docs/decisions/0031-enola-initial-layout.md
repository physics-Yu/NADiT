# 0031: Optional ENOLA initial reference layout

Status: Implemented and verified, 2026-10-22.

## Decision

The fixed-home compiler accepts two reference-layout providers:
`entry.kind=explicit` preserves the user's mapping, and `entry.kind=enola_initial`
runs the pinned ENOLA initial SA placer once. Both produce the existing
AtomLayout. CZ layers and retained measurements still return to this reference;
there is no dynamic SA or reference update during compilation.

The automatic provider selects one Device pair array (`array`, optional when
only one exists), assigns one qubit per cell to its left SLM trap, and leaves
the other trap free for gates. `seed`, `iterations`, and `moves_per_iteration`
are configurable, defaulting to 0, 100, and 400. Atom IDs follow physical qubit
indices. Explicit input continues to support arbitrary atom IDs and either
side of a cell.

Use the unmodified SAPlacer class from ENOLA commit
`2944dbf4e163e8d2eeeec607add0d9139edce689`, with private per-call RNG functions
and a budget adapter. Its cost is weighted Euclidean distance in cell-index
coordinates, with weights max(0.1, 1 - 0.1 * CZ_layer). It is not a transport
time objective. Device coordinates determine the emitted physical layout.
Keep the original finite-domain clipping and random perturbations. The only
algorithmic repair is zero-uphill warm-up: stop with the best sampled layout
instead of dividing by zero. This does not prove optimality. No-CZ inputs use
a deterministic row-major layout.

Initial scoring uses CZ layers derived from native DAG dependencies. A
measurement or conditional node blocks its dependent region; independent
chains remain available even if they occur later in source order. Scoring
neither samples a result nor chooses a branch. Initial resets and single-qubit
gates retain dependencies; they do not themselves add CZ depth. Compilation
later consumes the complete DAG through the existing controller.

## Interfaces

`placement/enola_initial.py` maps the native DAG and a Device array to AtomLayout.
`placement/enola_sa.py` adapts the pinned class and its deterministic budget.
Job loading invokes this provider once; the resolved entry settings are saved
in the job and actual generated positions are saved in result.entry_state.
Saved-result validation, visualization, and cost evaluation do not rerun SA.
Discovery and job JSON Schema describe both entry providers.

## Component boundary

Automatic atom-level SA does not preserve a code's geometric symmetry. QEC
templates can keep using explicit input for their internal layout. A later
component placer may optimize block anchors while retaining those local
coordinates; it will supply the same reference-layout boundary. No code-block
grouping or anchor optimizer is introduced by this change.

## Verification

Compare a bounded nondegenerate run with the original SAPlacer under the same
seed, shape, layers, and budget. Test private RNG state, flat/no-gate inputs,
capacity and array selection, prefix handling at feedback boundaries, explicit
input preservation, job roundtrip, one initialization per loaded job, and full
feedback compilation with exact final return and independent validation.

The full suite passes 1,666 tests against the 1,650-test baseline, with 16 new
cases and unchanged prior assertions. Formatting, lint, and mypy pass over
239 source/test/example files. A wheel build includes the exact pinned source
and license; loading the resources and running initial SA directly from that
wheel pass independently of the checkout source import.

The eight-atom example `examples/jobs/nonzoned-enola-initial.json` passes
saved-result semantic validation and exact entry/exit replay. With seed 0 and
the default search budget it produces two four-pair pulses, 34 steps, 18 atom
loads and stores, 320.792156 us transport, and 792.792156 us total planned time.
The explicit-layout example uses 332 us transport and 804 us total time, with
fewer transfer batches. This single example does not establish a transport-time
improvement guarantee for SA's distance objective. Outputs are under
`modules/physical_compiler/artifacts/enola-initial/`.
