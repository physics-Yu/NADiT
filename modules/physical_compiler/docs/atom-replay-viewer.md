# Atom replay viewer

The viewer consumes Device geometry and an existing AtomProgram. Exported
coordinates are micrometres and action times are microseconds.

## Generate and inspect

From `modules/physical_compiler`:

```text
uv run --locked python -m examples.visualize_compilation
uv run --locked python -m examples.visualize_compilation --output artifacts/my-run.html
```

The default output is `artifacts/atom-replay/index.html`, with `traces.json`
beside it. HTML includes all data, styles, and scripts and can be opened offline.
Select a trace, play or step through actions, and drag the within-step slider to
inspect transfers and simultaneous motion. The time field seeks in microseconds.
Paced playback gives each step visible time; proportional playback preserves
the relative durations. Neither mode changes the exported program times.

Scroll the plot to zoom, drag to pan, and click an atom to inspect its physical
qubit, position, and SLM/AOD holder. Toggle fixed sites, atom labels, movement
segments, or active AOD lines. A CZ pulse shows its illuminated coverage and
interacting pairs. Measurement records appear only at their compiled
availability time. The Atom IR panel preserves the original text.

Boundary observations list SLM atoms coincident with active AOD intersections.
Selecting one seeks to the recorded boundary and selects the atom. These
observations inspect endpoints; the displayed intermediate line segments are
interpolation between compiled endpoints, not a dynamics or trajectory solver.

## Export another compilation

```python
from nadit_physical_compiler.visualization import build_trace, write_html

trace = build_trace(
    result.program,
    entry,
    device,
    title="My compilation",
    measurements=result.measurements,
    operations=result.operations,
)
write_html([trace], "artifacts/my-compilation.html")
```

The exporter reads existing measurement records and does not call FakeBackend.
The compiler's retained-measurement replay boundary is preserved; destructive
measurement remains unsupported by the current `apply_step` implementation.

## Trace contract, version 1

An HTML document embeds `{ "schema_version": 1, "traces": [...] }` in a JSON
script element named `atom-replay-data`. Each trace has:

- `schema_version`, `title`, `description`, `duration_us`, `program_duration_us`.
- `device`: `name`, `zones` (`id`, `bounds`, `operations`), `slm_sites`
  (`id`, `x`, `y`), `aods` (`id`, `workspace`, `min_spacing`, `max_rows`,
  `max_columns`), and `cz_radius_um` (number or null). Bounds are
  `[x_min, y_min, x_max, y_max]`.
- `initial`: `atoms` and `grids`. Atom records contain `id`, `qubit` (nullable),
  `x`, `y`, and `holder`. Holders are `{kind:"slm",site_id}` or
  `{kind:"aod",aod_id,row,column}`. Grid records contain `aod_id`, `rows`,
  `columns`; each line is `{id,coordinate}`.
- `steps`: `index`, `start_us`, `duration_us`, and `actions`.
- Each action: `index` (global action index), `kind` (Atom IR class name),
  `duration_us`, `source_ids`, `atoms`, `text`, `parameters`, `coverage`
  (null or `{regions:[bounds...],atoms:[ids...]}`), `pairs` (illuminated CZ
  pairs), and `patch` (`atoms`, `grids`). Empty grids remove active lines.
- `measurements`: `{measurement_id,value,available_at_us}`. These are compiled
  records, never sampled by the viewer.
- `operations`: `{operation_id,status,start_us,end_us}`.
- `issues`: `{step_index,time_us,kind,atom_id,aod_id,x,y,message}` for SLM atoms
  on active intersections at step boundaries. Initial observations use -1.
- `atom_ir`: the original Stim-style program text.

All action patches in a step use the common step-entry layout. MOVE patches
contain target atoms and the complete destination grid; interpolate their
coordinates from entry values by stable IDs. LOAD and STORE patches apply at
action completion. Other actions have empty spatial patches. A completed step
applies all its patches. State changes do not depend on action serialization
order.

The final timeline may extend beyond the last action to display pending
measurement availability. Zero-duration steps remain individually selectable.
The browser keeps sparse checkpoints and replays intervening patches for seeks.

## Validation

Python tests reconstruct every transport boundary from sparse patches and check
parallel durations, holder changes, coverage, recorded results, and observations.
The DOM-independent browser model is checked separately:

```text
node --test tests/web/replay-model.test.cjs
```

The initial release has 1,112 passing Python tests and 10 passing Node tests.
Fourteen sampled boundaries across the three real compilations also match
Python replay exactly, including reverse seeks. Actual browser inspection
covered parallel motion, individual atom coordinates and holders, CZ pairs,
measurement availability, playback, and diagnostic-boundary navigation.

The generated diagnostic fixture used for UI inspection is hand-authored and
separate from the three compiler-generated examples. It verifies that an empty
AOD intersection reaching a stationary SLM atom can be located in the viewer.

See [ADR 0022](../../../docs/decisions/0022-atom-replay-viewer.md).

## Package boundary

`visualization.trace.build_trace(program, entry_state, device, *, title,
description="", measurements=(), operations=())` creates the trace.
`visualization.export.write_html(traces, path, *, title="NADiT Atom Replay")`
writes one self-contained document. Browser assets live in `visualization/web`.
The viewer model is independent of DOM rendering and can be tested with Node.
