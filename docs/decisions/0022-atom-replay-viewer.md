# 0022: Atom IR replay viewer

Status: Implemented on 2026-10-21.

## Context

Placement and transport comparisons now have inspectable Atom IR. An animated
view should expose atom motion, transfer ownership, active AOD intersections,
pulse coverage, and measurement feedback at the same action boundaries.

## Decision

Add an offline HTML viewer under the physical compiler's visualization package.
Export the existing Device, entry AtomLayout, and AtomProgram. Use apply_step
for boundary states and store only changed atoms and grids. The compiler and
its scheduling decisions remain the source of the program.

The browser interpolates MOVE endpoints and stable AOD line coordinates for
display. Transfers change ownership on completion. Each parallel action uses
its own duration within the step. The interface exposes both step position and
physical time, with paced playback so short pulses can be inspected.

Show static SLM sites and zone bounds, complete active AOD rows and columns,
atom IDs and holders, pulse coverage, source operation IDs, and measurement
records when their recorded availability time is reached. Support playback,
single stepping, seeking, zoom/pan, atom selection, and Atom IR text inspection.

Export endpoint occupied-intersection observations for navigation. These
observations run in the visualization export, outside compiler search. Keep
display interpolation distinct from a dynamics model or an inferred path.

Use self-contained HTML/CSS/JavaScript with no added dependencies. Provide
compiled feedback examples for both outcomes and a larger parallel circuit.
The Python exporter, browser timeline model, rendering, and example generation
are separate modules.

The ADR 0021 adapter accepts `nadit.result/1` and Device JSON through
`build_result_trace`. The example command's `--result` path uses this adapter
without reading QASM or invoking compilation. It retains the same browser
trace format and sparse replay model.

## Verification

Compare exported boundary patches with apply_step and the compiler exit state.
Cover unequal parallel durations, zero-duration steps, grid line identity,
transfer completion, CZ coverage, result latency, and occupied intersections.
Check actual browser controls and rendered frames, then run the full suite.

Completed: 1,112 Python tests pass against the 1,096-test baseline, plus 10
Node replay-model tests. Ruff and mypy pass. Fourteen boundaries from the three
actual compiler examples match Python replay exactly in the browser model,
including reverse seeks. A clean-source wheel contains all four viewer assets.

Browser inspection covers parallel MOVE and active lines, atom selection,
whole-layer CZ, playback/pause, time seeking across measurement availability,
and navigation to an occupied-intersection diagnostic fixture. Pulse rendering
and spatial patches share each action's completion boundary, including short
pulses parallel to longer movements.

## Alternatives

A video cannot inspect atom identity or seek precise action boundaries.
Recompiling in the viewer would obscure which program is being inspected.

## Consequences

The same export can be viewed offline and shared as one HTML file. Rendered
motion is linear endpoint interpolation; numerical simulation is unchanged.
