/* Resource/action schedule and completion-booked loss; independent of drawing. */
(function(root) {
  'use strict';
  function upperBound(xs, time) {
    let low = 0, high = xs.length;
    while (low < high) { const mid = (low + high) >>> 1; if (xs[mid].end <= time) low = mid + 1; else high = mid; }
    return low;
  }
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
      this.loss = new Map(trace.initial.atoms.map(a => [a.id, []]));
      this.hasNoise = !!trace.noise_report;
      for (const event of trace.noise_report?.noise.loss.events || []) {
        this.loss.get(event.atom).push({end:event.start_us + event.duration_us, log:Math.log(event.survival_probability)});
      }
      for (const events of this.loss.values()) {
        events.sort((a,b) => a.end - b.end);
        let sum = 0;
        for (const event of events) { sum += event.log; event.total = sum; }
      }
      this.finalRisk = this.riskAt(trace.duration_us);
      this.maxAtomLoss = Math.max(1e-9, ...this.finalRisk?.atoms.values() || []);
      const times = [...new Set([0, trace.duration_us, ...[...this.loss.values()].flat().map(e => e.end)])].sort((a,b) => a-b);
      this.curve = times.map(time => [time, this.riskAt(time)?.expected || 0]);
    }
    riskAt(time) {
      if (!this.hasNoise) return null;
      const atoms = new Map(); let total = 0, expected = 0;
      for (const [id, events] of this.loss) {
        const index = upperBound(events, time) - 1;
        const log = index < 0 ? 0 : events[index].total;
        const loss = -Math.expm1(log);
        atoms.set(id, loss); expected += loss; total += log;
      }
      return {atoms, expected, allSurvive:Math.exp(total)};
    }
    hit(lane, time) {
      return this.lanes[lane]?.events.find(e => e.start <= time && (time < e.end || e.start === e.end && time === e.start));
    }
  }
  const api = {Analysis};
  root.AtomReplayAnalysis = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
