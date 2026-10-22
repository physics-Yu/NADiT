const test = require('node:test');
const assert = require('node:assert/strict');
const {Analysis} = require('../../src/nadit_physical_compiler/visualization/web/analysis.js');
const trace = {
  duration_us: 12,
  initial: {atoms: [{id: 7}, {id: 9}]},
  steps: [{start_us: 0, duration_us: 10, actions: [
    {index: 0, kind: 'Move', duration_us: 10, atoms: [7], source_ids: [], parameters: {aod_id:'a'}},
    {index: 1, kind: 'Rotation', duration_us: 2, atoms: [9], source_ids: [1], parameters: {channel:'single'}}
  ]}],
  noise_report: {noise: {loss: {events: [
    {atom:7, start_us:0, duration_us:10, survival_probability:0.9},
    {atom:7, start_us:2, duration_us:0, survival_probability:0.8},
    {atom:9, start_us:10, duration_us:2, survival_probability:0.5}
  ]}, gate_diagnostics: [], channels: []}}
};
test('schedule uses individual action durations and click hit targets', () => {
  const model = new Analysis(trace);
  assert.equal(model.lanes[1].events[0].end, 2);
  assert.equal(model.hit(1, 1).action.index, 1);
  assert.equal(model.hit(1, 3), undefined);
  assert.equal(model.lanes[0].events[0].end, 10);
});
test('loss books at completion, multiplies, and supports backward seeking', () => {
  const model = new Analysis(trace);
  assert.equal(model.riskAt(0).expected, 0);
  assert.ok(Math.abs(model.riskAt(2).expected - .2) < 1e-12);
  const last = model.riskAt(12);
  assert.ok(Math.abs(last.expected - .78) < 1e-12);
  assert.ok(Math.abs(last.allSurvive - .36) < 1e-12);
  assert.ok(Math.abs(last.atoms.get(7) - .28) < 1e-12);
  assert.equal(model.riskAt(0).expected, 0);
});
test('certain loss remains finite and unavailable noise is distinct from zero', () => {
  const copy = structuredClone(trace);
  copy.noise_report.noise.loss.events[0].survival_probability = 0;
  const model = new Analysis(copy);
  assert.equal(model.riskAt(10).atoms.get(7), 1);
  assert.equal(model.riskAt(10).allSurvive, 0);
  delete copy.noise_report;
  assert.equal(new Analysis(copy).riskAt(10), null);
});
