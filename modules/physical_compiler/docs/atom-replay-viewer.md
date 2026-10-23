# Atom replay viewer

The viewer consumes Device geometry and an existing AtomProgram. Exported
coordinates are micrometres and action times are microseconds.

## Generate and inspect

From `modules/physical_compiler`:

```text
uv run --locked python -m examples.visualize_compilation --result result.json --output artifacts/saved-result.html
uv run --locked python -m examples.visualize_compilation
uv run --locked python -m examples.visualize_compilation --output artifacts/my-run.html
```

`--result` reads `nadit.result/2` with its embedded Device snapshot, so the
original QASM and Device files may be moved or removed. Repeat it for multiple
saved cases. Version-1 results remain supported through their `job.device`
reference. `--device DEVICE.json` explicitly replaces the Device for either
version. This mode does not read QASM, compile, or sample measurements. See
[Jobs and saved results](jobs-and-results.md) for producing result files.

The default output is `artifacts/atom-replay/index.html`, with `traces.json`
beside it. HTML includes all data, styles, and scripts and can be opened offline.
Select a trace, play or step through actions, and drag the within-step slider to
inspect transfers and simultaneous motion. The time field seeks in microseconds.
Paced playback gives each step visible time; proportional playback preserves
the relative durations. Neither mode changes the exported program times.

## Program strip and presentation

The step slider has one colour segment per Atom IR step, plus the end/feedback
tail position. Load, Move, Store, CZ, local control, readout and wait have a
labelled colour key. Hover over the strip to preview a step's actions, atom
count and recorded time interval without seeking; drag or use the range control
to select it. Mixed steps list every action kind; their colour prioritizes CZ,
readout, transport, local control, then wait. Segments represent steps, not
elapsed-time proportions; the execution schedule below remains time-scaled.

Storage regions use a faint neutral fill and light empty-site markers. Active
atoms, AOD lines, gates and readout retain the existing functional colours.

Click **Present** or press **F** to show the stage full screen. Press **Esc**,
**F**, or **Exit full screen** to return. Playback position and play/pause state
are preserved. If the host denies or lacks browser fullscreen, the same stage
fills the application window. The HUD displays the current step, action, time,
and mean/maximum atom loss when a noise report is available. The camera refits
around the HUD and controls. Atoms, SLM sites, AOD spots, spatial line widths and
halos scale proportionally with the scene, including after viewport fitting or
presentation changes. They have no minimum/maximum pixel radius. Their radii
are schematic scene sizes, not physical atom sizes or calibrated beam waists.
Display radii are 0.95 for atoms, 0.65 for SLM sites and 0.4 for AOD spots in
scene units; AOD lines use a 0.2 scene-unit width. These presentation constants
do not change the Device geometry or interaction radius.
The **Display size** row provides independent Atoms, SLM sites and AOD spots
sliders, from 25% to 300% in 5% increments. 100% uses these default radii.
**Reset sizes** restores all three to 100%. Radii combine the selected display
multiplier with camera scaling, so zoom remains proportional at every setting.
Atom outlines/highlights follow atom size; AOD beam line width is independent
of the spot slider. Controls are available during fullscreen presentation too.
Adjustments do not seek, pause, resample or modify the trace. They persist when
switching traces or entering/exiting fullscreen in the current page; reloading
the page restores defaults. Exported Device/Atom IR/result data are unchanged.
Buttons, labels and HUD text keep their CSS-pixel sizes. Browser/OS accessibility
zoom still scales the page normally.

During presentation playback, controls fade after 2.5 seconds without input.
Pointer movement, keyboard input or focus restores them; paused playback keeps
them visible. Space toggles playback, arrow keys step, and L toggles loss colour.
Editable fields keep their native keyboard behavior; Esc still exits presentation.
The browser's native fullscreen gesture/permissions remain in effect.

This is packaged in the common viewer, adapted from the supplied
`prototypes/atom-replay-redesign` layout. It does not change trace data, motion
endpoints, timing, loss attribution or compiler algorithms. DOM/canvas tests
cover controls and fullscreen transitions; they do not establish browser visual
acceptance. Browser review of local file URLs is currently blocked by the tool's
URL policy, so public-display visual acceptance remains a manual check.

`Path: Lanes` is the default display: a small diagonal departure, vertical
travel in an offset column, horizontal alignment in an offset row, and vertical
arrival. The AOD lines and all carried atoms share segment progress, including
carried atoms omitted from sparse patches because their endpoints are unchanged.
Dashed trails follow this same path. `Path: Direct` restores straight lines.
Offsets use one quarter of the smallest coordinate gap, capped at 2 um and
reduced or reversed to fit the workspace. If no common offset fits, the legend
identifies direct-preview fallback. These are display lanes, not compiled
collision-free trajectories; pulse timing and boundary observations are unchanged.

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

For a saved version-2 result:

```python
import json
from pathlib import Path

from nadit_physical_compiler.visualization import build_result_trace, write_html

document = json.loads(Path("result.json").read_text(encoding="utf-8"))
trace = build_result_trace(document, title="Saved compilation")
write_html([trace], "artifacts/saved-compilation.html")
```

An explicit second argument, `device_document`, overrides the embedded
Device; the Python adapter requires it for a version-1 result. The example
CLI resolves that legacy Device reference before calling the adapter.

Direct compilation records remain supported:

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

Run `nadit validate RESULT.json` for the independent transport, pulse,
addressing, dependency, and feedback checks before interpreting a version-2
trace. The viewer exports recorded validation metadata when present; viewing
a trace does not run validation. Its boundary observations remain the focused
SLM/AOD intersection checks described above.

Python tests reconstruct every transport boundary from sparse patches and check
parallel durations, holder changes, coverage, recorded results, and observations.
The DOM-independent browser model is checked separately:

```text
node --test tests/web/*.test.cjs
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
`visualization.build_result_trace(document, device_document=None, *,
title="Saved compilation", description="")` adapts saved result JSON and uses
its embedded Device when the second argument is omitted.
`visualization.export.write_html(traces, path, *, title="NADiT Atom Replay")`
writes one self-contained document. Browser assets live in `visualization/web`.
The viewer model is independent of DOM rendering and can be tested with Node.
