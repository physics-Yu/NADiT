const test = require('node:test');
const assert = require('node:assert/strict');
const { Replay } = require('../../src/nadit_physical_compiler/visualization/web/model.js');
const { actionProgress } = require('../../src/nadit_physical_compiler/visualization/web/model.js');

const atom = (id, x, holder = { kind: 'aod', aod_id: 'a', row: 2, column: 4 }) =>
  ({ id, qubit: id, x, y: 3, holder });
const grid = (x) => ({ aod_id: 'a', rows: [{ id: 2, coordinate: 3 }], columns: [{ id: 4, coordinate: x }, { id: 8, coordinate: x + 2 }] });
const action = (kind, duration_us, atoms = [], grids = []) =>
  ({ kind, duration_us, atoms: atoms.map(a => a.id), patch: { atoms, grids } });
function fixture() {
  return {
    initial: { atoms: [atom(0, 0), atom(1, 20)], grids: [grid(0)] },
    steps: [
      { start_us: 0, duration_us: 10, actions: [action('Move', 5, [atom(0, 10)], [grid(10)]), action('Store', 10, [atom(1, 20, { kind: 'slm', site_id: 's1' })])] },
      { start_us: 10, duration_us: 0, actions: [action('Store', 0, [atom(1, 20, { kind: 'slm', site_id: 'zero' })])] },
      { start_us: 10, duration_us: 10, actions: [action('Move', 10, [atom(0, 30)], [grid(30)])] },
    ],
    program_duration_us: 20, duration_us: 25,
    measurements: [{ measurement_id: 9, value: -1, available_at_us: 23 }],
  };
}

test('parallel movement uses own duration and stable grid line identities', () => {
  const replay = new Replay(fixture());
  const frame = replay.frame(0, 0.25);
  assert.equal(frame.atoms.get(0).x, 5);
  assert.equal(frame.grids.get('a').columns[1].coordinate, 7);
  assert.equal(frame.atoms.get(1).holder.kind, 'aod');
  assert.equal(replay.frame(0, 0.75).atoms.get(0).x, 10);
  assert.equal(replay.frame(0, 1).atoms.get(1).holder.kind, 'slm');
  assert.equal(replay.frame(0, 0.1).atoms.get(1).holder.kind, 'aod');
});

test('zero-duration step is selectable and its patch replays at completion', () => {
  const replay = new Replay(fixture());
  assert.equal(replay.frame(1, 0).atoms.get(1).holder.site_id, 's1');
  assert.equal(replay.frame(1, 1).atoms.get(1).holder.site_id, 'zero');
  assert.equal(replay.frame(2, 0).atoms.get(1).holder.site_id, 'zero');
  assert.equal(replay.frame(0, 0).atoms.get(1).holder.kind, 'aod');
});

test('measurement records are hidden until availability including final tail', () => {
  const replay = new Replay(fixture());
  assert.deepEqual(replay.frame(2, 1).measurements, []);
  assert.deepEqual(replay.cursorAtTime(22), { step: 3, fraction: 0.4 });
  assert.equal(replay.frame(3, 0.4).measurements.length, 0);
  assert.equal(replay.frame(3, 0.6).measurements[0].value, -1);
  assert.deepEqual(replay.cursorAtTime(15), { step: 2, fraction: 0.5 });
});

test('sparse checkpoints preserve exact end state and entry after long reverse seeks', () => {
  const trace = fixture();
  trace.steps = Array.from({ length: 150 }, (_, i) => ({ start_us: i, duration_us: 1, actions: [action('Move', 1, [atom(0, i + 1)], [grid(i + 1)])] }));
  trace.program_duration_us = trace.duration_us = 150;
  const replay = new Replay(trace);
  assert.equal(replay.checkpoints.size, 3);
  assert.equal(replay.frame(149, 1).atoms.get(0).x, 150);
  assert.equal(replay.frame(64, 0.5).atoms.get(0).x, 64.5);
  assert.equal(replay.frame(3, 0).atoms.get(0).x, 3);
  assert.equal(trace.initial.atoms[0].x, 0);
});

test('load and empty-grid store occur at completion and reverse correctly', () => {
  const trace = fixture();
  trace.initial = { atoms: [atom(0, 0, { kind: 'slm', site_id: 's' })], grids: [] };
  trace.steps = [
    { start_us: 0, duration_us: 5, actions: [action('Load', 5, [atom(0, 0)], [grid(0)])] },
    { start_us: 5, duration_us: 5, actions: [action('Store', 5, [atom(0, 0, { kind: 'slm', site_id: 's' })], [{ aod_id: 'a', rows: [], columns: [] }])] },
  ];
  trace.program_duration_us = trace.duration_us = 10;
  const replay = new Replay(trace);
  assert.equal(replay.frame(0, 0.5).grids.size, 0);
  assert.equal(replay.frame(0, 1).grids.get('a').rows.length, 1);
  assert.equal(replay.frame(1, 1).grids.has('a'), false);
  assert.equal(replay.frame(1, 0).grids.get('a').columns.length, 2);
});

test('empty program exposes an empty final state and accepts absolute-time seeking', () => {
  const replay = new Replay({ initial: { atoms: [], grids: [] }, steps: [], measurements: [], program_duration_us: 0, duration_us: 0 });
  assert.deepEqual(replay.cursorAtTime(0), { step: 0, fraction: 1 });
  assert.equal(replay.frame(0, 0).atoms.size, 0);
  assert.equal(replay.frame(0, 1).time_us, 0);
});

test('grid interpolation follows line IDs even when destination order changes', () => {
  const trace = fixture();
  trace.steps[0].actions[0].patch.grids[0].columns.reverse();
  const frame = new Replay(trace).frame(0, 0.25);
  const coordinates = new Map(frame.grids.get('a').columns.map(line => [line.id, line.coordinate]));
  assert.equal(coordinates.get(4), 5);
  assert.equal(coordinates.get(8), 7);
});

test('zero-duration parallel action completes at start of a nonzero step', () => {
  const trace = fixture();
  trace.steps[0].actions[1].duration_us = 0;
  assert.equal(new Replay(trace).frame(0, 0).atoms.get(1).holder.kind, 'slm');
});

test('seeking backward hides previously available measurement values', () => {
  const replay = new Replay(fixture());
  assert.equal(replay.frame(3, 1).measurements.length, 1);
  assert.deepEqual(replay.frame(0, 1).measurements, []);
});

test('pulse highlights and patches share the individual action completion boundary', () => {
  assert.equal(actionProgress(0.5, 100, 0.25, 0.0025), 0.5);
  assert.equal(actionProgress(0.5, 100, 0.5, 0.005), 1);
  assert.equal(actionProgress(0.5, 100, 50, 0.5), 1);
  assert.equal(actionProgress(100, 100, 50, 0.5), 0.5);
  assert.equal(actionProgress(0, 100, 0, 0), 1);
  assert.equal(actionProgress(0, 0, 0, 0), 0);
  assert.equal(actionProgress(0, 0, 0, 1), 1);
});
