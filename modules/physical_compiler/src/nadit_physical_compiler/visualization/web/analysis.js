/* Resource/action schedule and completion-booked loss; independent of drawing. */
(function(root) {
  'use strict';
  const {LossIndex} = typeof module !== 'undefined' && module.exports ? require('./loss.js') : root.AtomReplayLoss;
  class Analysis {
    constructor(trace) {
      this.trace = trace;
      const lanes = new Map();
      trace.steps.forEach((step, stepIndex) => step.actions.forEach(action => {
        const resource = action.parameters.aod_id || action.parameters.channel || 'timing';
        const id = resource + ' · ' + action.kind;
        if (!lanes.has(id)) lanes.set(id, {label:id, kind:action.kind, events:[]});
        lanes.get(id).events.push({start:step.start_us, end:step.start_us + action.duration_us, step:stepIndex, action});
      }));
      this.lanes = [...lanes.values()];
      this.hasNoise = !!trace.noise_report;
      this.loss = new LossIndex(trace.initial.atoms.map(a => a.id),trace.noise_report?.noise.loss.events || []);
      this.finalRisk = this.riskAt(trace.duration_us);
      this.maxAtomLoss = Math.max(1e-9, ...this.finalRisk?.atoms.values() || []);
      const times = [...new Set([0, trace.duration_us, ...this.loss.times])].sort((a,b) => a-b);
      this.curve = times.map(time => [time, this.riskAt(time)?.expected || 0]);
    }
    riskAt(time) {
      if (!this.hasNoise) return null;
      return this.loss.at(time);
    }
    hit(lane, time) {
      return this.lanes[lane]?.events.find(e => e.start <= time && (time < e.end || e.start === e.end && time === e.start));
    }
  }
  const api = {Analysis};
  root.AtomReplayAnalysis = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
