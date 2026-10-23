# Atom replay redesign (prototype, not packaged)

Status: visual redesign prototype, 2026-10-23 (light theme, rebased on the loss/schedule viewer). **Not integrated into the
package.** The shipped viewer in
`src/nadit_physical_compiler/visualization/web/` is unchanged. This folder is
the reference for a later integration pass.

## Example

Open in a browser (offline, single file):

`modules/physical_compiler/prototypes/atom-replay-redesign/atom-replay-redesign.html`

It contains four real, semantically validated `nadit.result/2` traces, all
selectable from the "Trace" menu:

| Trace | Source job | Shows |
| --- | --- | --- |
| Capacity, 20 CZ, flexible AOD | motion_capacity.qasm3 on configs/zoned_flexible.json, IDS + strict | 40 atoms, wide parallel moves |
| Capacity, 20 CZ, rigid 16×16 AOD | same circuit on configs/zoned_rigid_16.json | many smaller rigid batches |
| Multipair feedback, Search Smarter | examples/jobs/multipair.json | CZ pulses, readout, feedback wait |
| Shared array, fixed homes (nonzoned) | examples/jobs/nonzoned.json | ADR 0030 fixed-home compiler |

Rebuild from the saved results in `inputs/`:

```text
PYTHONPATH=src python prototypes/atom-replay-redesign/build_example.py \
  prototypes/atom-replay-redesign/atom-replay-redesign.html \
  "prototypes/atom-replay-redesign/inputs/_cap_zoned_flexible.result.json=Capacity, 20 CZ, flexible AOD" \
  "prototypes/atom-replay-redesign/inputs/_cap_zoned_rigid_16.result.json=Capacity, 20 CZ, rigid 16×16 AOD" \
  "prototypes/atom-replay-redesign/inputs/multipair.result.json=Multipair feedback, Search Smarter" \
  "prototypes/atom-replay-redesign/inputs/nonzoned.result.json=Shared array, fixed homes (nonzoned)"
```

`build_example.py` performs the same template substitution as
`visualization.export.write_html`, but reads assets from `./web/`. Traces come
from the unchanged `build_result_trace`.

## What changed

Presentation only, on top of the current shipped viewer (branch
`codex/configuration-reference`, including the schedule and loss views from
`1951cb5` and `661cdf7`). The trace format and `paths.js`, `model.js`,
`loss.js`, `analysis.js`, `loss-panel.js`, `dashboard.js` are byte-for-byte the
shipped versions. Every element ID read by those scripts and by the tests
(`atom-replay-data`, all control IDs, all `risk-*`, `loss-*`, `schedule-*`,
`noise-*` IDs) is kept.

- **Original light palette.** Same colours as the shipped viewer: ink
  `#243748`, teal `#147b7b` for AOD atoms, beams and controls, SLM blue
  `#4772b1`, CZ purple `#9b65a8`, readout amber `#b96c25`, local gates gold
  `#c58b35`, loss red `#b25138`. The stage is a near-white field with a light
  grid; atoms are solid dots with a white rim.
- **Program strip.** The step scrubber is a strip with one segment per step,
  coloured by the step's dominant action (CZ > readout > transport > local >
  wait). Played steps are solid, future steps faded. It is still the step
  slider.
- **Stage HUD.** Current step, action kinds and time sit on the stage. When a
  noise report is attached, the HUD also shows expected lost atoms and mean
  atom loss so far, so the cost is visible in full screen.
- **Zones** are tinted by function and labelled to their left, so labels never
  cover atoms.
- **Loss colour** (existing `risk-toggle`) can also be switched with **L**. The
  highest-loss atom is ringed and labelled above the atom when loss colour is
  on, and always in presentation.
- **Inspector order:** current step, atom loss estimate, noise in this step,
  atom, measurements, boundary observations. The schedule and loss-by-source
  table sit in panels under the workspace.
- **Typography and copy.** Bahnschrift (built into Windows; DIN Alternate,
  Barlow and system fallbacks elsewhere), tabular numerals, sentence-case
  labels instead of tracked capitals.
- **Action cards** carry a coloured left edge per action kind, matching the
  stage and strip.

The four example results in `inputs/` now carry a `noise/v1.1` report computed
with `cost_params/noise_illustrative.json` (illustrative, not calibrated), so
the loss panels are populated.

## Full-screen presentation

- "Present" button in the top bar, or press **F**. **Esc** or F again exits;
  an "Exit full screen" button is shown on the stage.
- Uses the browser Fullscreen API on the stage element; if it is unavailable
  (embedded preview), the stage covers the window instead.
- In presentation, atoms, beams, pulse links, labels and HUD text are scaled
  ×1.7, and the layout is re-fitted into the band between the HUD and the
  controls.
- Legend and playback controls overlay the bottom of the stage and fade out
  after 2.5 s without mouse or keyboard input (the cursor hides too); any
  movement or key brings them back. Space, ← and → keep working.
- Reduced-motion preference disables the fade.

## Integration notes for the follow-up (Codex)

1. Replace `viewer.html`, `viewer.css`, `viewer.js` in
   `src/nadit_physical_compiler/visualization/web/` with the files in `web/`.
   The other JS modules need no change.
2. `write_html` needs no change: the template placeholders and asset order are identical.
3. Run the existing suite, including `tests/web/*.test.cjs`
   (`replay-model`, `loss-model`, `analysis-model`, `dashboard`) and
   `tests/test_visualization_noise.py`.
4. Add a browser check for presentation mode (enter with F, controls hidden
   after idle, Esc restores layout and re-fits) and for the step strip colour
   of a CZ step.
5. Packaging the entry point (`nadit view`, experiment `--viewers`) is a
   separate task and intentionally not part of this prototype.

Known limits: all trace data is still inlined, so very large programs (for
example d = 7 factories) will produce large files; atoms and halos are drawn
per frame without culling beyond the viewport.
