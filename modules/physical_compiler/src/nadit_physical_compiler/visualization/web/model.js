/* DOM-independent replay. Spatial patches use the common step-entry state. */
(function (root) {
  'use strict';
  const CHECKPOINT_STRIDE = 64;
  const paths = typeof module !== 'undefined' && module.exports ? require('./paths.js') : root.AtomReplayPaths;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const copyState = (state) => ({ atoms: new Map(state.atoms), grids: new Map(state.grids) });
  function actionProgress(durationUs, stepDurationUs, elapsedUs, stepFraction) {
    if (stepDurationUs === 0) return stepFraction === 1 ? 1 : 0;
    return durationUs === 0 ? 1 : clamp(elapsedUs / durationUs, 0, 1);
  }
  function initialState(initial) {
    return {
      atoms: new Map(initial.atoms.map(atom => [atom.id, atom])),
      grids: new Map(initial.grids.map(grid => [grid.aod_id, grid])),
    };
  }
  function applyPatch(state, patch) {
    for (const atom of patch.atoms) state.atoms.set(atom.id, atom);
    for (const grid of patch.grids) {
      if (!grid.rows.length && !grid.columns.length) state.grids.delete(grid.aod_id);
      else state.grids.set(grid.aod_id, grid);
    }
  }
  function interpolateLines(source, target, fraction) {
    const coordinates = new Map(source.map(line => [line.id, line.coordinate]));
    return target.map(line => ({ id: line.id, coordinate: (coordinates.get(line.id) ?? line.coordinate) * (1 - fraction) + line.coordinate * fraction }));
  }
  class Replay {
    constructor(trace, { motion = 'direct' } = {}) {
      this.trace = trace;
      this.motion = motion;
      this.movePaths = new Map();
      this.checkpoints = new Map();
      const state = initialState(trace.initial);
      this.checkpoints.set(0, copyState(state));
      trace.steps.forEach((step, index) => {
        step.actions.forEach(action => applyPatch(state, action.patch));
        if ((index + 1) % CHECKPOINT_STRIDE === 0) this.checkpoints.set(index + 1, copyState(state));
      });
      this.cachedStep = -1;
      this.entry = state;
      this.display = state;
    }
    entryAt(stepIndex) {
      const checkpointIndex = Math.floor(stepIndex / CHECKPOINT_STRIDE) * CHECKPOINT_STRIDE;
      const state = copyState(this.checkpoints.get(checkpointIndex));
      for (let i = checkpointIndex; i < stepIndex; i++) {
        for (const action of this.trace.steps[i].actions) applyPatch(state, action.patch);
      }
      return state;
    }
    cursorAtTime(timeUs) {
      const time = clamp(timeUs, 0, this.trace.duration_us);
      if (time >= this.trace.program_duration_us) {
        const tail = this.trace.duration_us - this.trace.program_duration_us;
        return { step: this.trace.steps.length, fraction: tail > 0 ? (time - this.trace.program_duration_us) / tail : 1 };
      }
      let low = 0, high = this.trace.steps.length;
      while (low < high) {
        const mid = (low + high) >>> 1;
        if (this.trace.steps[mid].start_us <= time) low = mid + 1;
        else high = mid;
      }
      const step = Math.max(0, low - 1);
      const record = this.trace.steps[step];
      return { step, fraction: record.duration_us ? clamp((time - record.start_us) / record.duration_us, 0, 1) : 1 };
    }
    frame(stepIndex, fraction = 0) {
      const index = clamp(Math.floor(stepIndex), 0, this.trace.steps.length);
      const part = clamp(fraction, 0, 1);
      if (index !== this.cachedStep) {
        this.cachedStep = index;
        this.entry = this.entryAt(index);
        this.display = copyState(this.entry);
        this.movePaths = new Map((this.trace.steps[index]?.actions || [])
          .filter(action => action.kind === 'Move')
          .map(action => [action, paths.buildMovePath(action, this.entry, this.trace.device, this.motion)]));
      }
      const step = this.trace.steps[index];
      const elapsed = step ? step.duration_us * part : (this.trace.duration_us - this.trace.program_duration_us) * part;
      const time = (step ? step.start_us : this.trace.program_duration_us) + elapsed;
      const actions = step ? step.actions : [];
      for (const action of actions) {
        // Reset only touched records so backward scrubbing does not copy all atoms.
        if (action.kind === 'Move') {
          for (const id of this.movePaths.get(action).atoms.keys()) this.display.atoms.set(id, this.entry.atoms.get(id));
        }
        for (const target of action.patch.atoms) {
          if (this.entry.atoms.has(target.id)) this.display.atoms.set(target.id, this.entry.atoms.get(target.id));
          else this.display.atoms.delete(target.id);
        }
        for (const target of action.patch.grids) {
          if (this.entry.grids.has(target.aod_id)) this.display.grids.set(target.aod_id, this.entry.grids.get(target.aod_id));
          else this.display.grids.delete(target.aod_id);
        }
      }
      for (const action of actions) {
        const progress = actionProgress(action.duration_us, step.duration_us, elapsed, part);
        if (progress === 1) {
          applyPatch(this.display, action.patch);
        } else if (action.kind === 'Move') {
          if (this.motion === 'lanes') {
            const sample = paths.sampleMovePath(this.movePaths.get(action), progress);
            for (const [id, position] of sample.atoms) this.display.atoms.set(id, { ...this.entry.atoms.get(id), ...position });
            for (const [id, grid] of sample.grids) this.display.grids.set(id, grid);
            continue;
          }
          for (const target of action.patch.atoms) {
            const source = this.entry.atoms.get(target.id);
            this.display.atoms.set(target.id, { ...source, x: source.x + (target.x - source.x) * progress, y: source.y + (target.y - source.y) * progress });
          }
          for (const target of action.patch.grids) {
            const source = this.entry.grids.get(target.aod_id) || { rows: [], columns: [] };
            this.display.grids.set(target.aod_id, { aod_id: target.aod_id, rows: interpolateLines(source.rows, target.rows, progress), columns: interpolateLines(source.columns, target.columns, progress) });
          }
        }
      }
      return {
        ...this.display, step: index, fraction: part, time_us: time, elapsed_us: elapsed,
        actions, entry: this.entry, movePaths: this.movePaths,
        measurements: (this.trace.measurements || []).filter(record => record.available_at_us <= time),
      };
    }
  }
  const api = { Replay, CHECKPOINT_STRIDE, actionProgress };
  root.AtomReplayModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
