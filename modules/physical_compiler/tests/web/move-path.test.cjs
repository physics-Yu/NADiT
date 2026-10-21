const test = require('node:test');
const assert = require('node:assert/strict');
const { Replay } = require('../../src/nadit_physical_compiler/visualization/web/model.js');
const { buildMovePath, sampleMovePath } = require('../../src/nadit_physical_compiler/visualization/web/paths.js');

function fixture() {
  const atom = (id, x, y) => ({ id, x, y, holder: { kind: 'aod', aod_id: 'a', row: 7, column: id } });
  const grid = (y, xs) => ({ aod_id: 'a', rows: [{ id: 7, coordinate: y }], columns: xs.map((x, id) => ({ id, coordinate: x })) });
  const initial = { atoms: [atom(0, 0, 0), atom(1, 5, 0)], grids: [grid(0, [0, 5])] };
  const action = { kind: 'Move', duration_us: 10, patch: { atoms: [atom(0, 10, 20), atom(1, 15, 20)], grids: [grid(20, [10, 15])] } };
  const device = { slm_sites: [{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 20 }, { x: 15, y: 20 }, { x: 5, y: 10 }], aods: [{ id: 'a', workspace: [-10, -10, 40, 40] }] };
  const trace = { initial, device, steps: [{ start_us: 0, duration_us: 20, actions: [action] }], duration_us: 20, program_duration_us: 20, measurements: [] };
  const entry = { atoms: new Map(initial.atoms.map(a => [a.id, a])), grids: new Map(initial.grids.map(g => [g.aod_id, g])) };
  return { trace, entry, action, device };
}

test('diagonal departure is followed only by axis-aligned segments', () => {
  const { action, entry, device } = fixture();
  const path = buildMovePath(action, entry, device);
  assert.equal(path.mode, 'lanes');
  const points = path.atoms.get(0);
  assert.ok(points[1].x !== points[0].x && points[1].y !== points[0].y);
  for (let i = 2; i < points.length; i++) {
    assert.ok(points[i].x === points[i - 1].x || points[i].y === points[i - 1].y);
  }
  assert.deepEqual(points[0], { x: 0, y: 0 });
  assert.deepEqual(points.at(-1), { x: 10, y: 20 });
});

test('all atoms and lines share phase and preserve rigid spacing', () => {
  const { action, entry, device } = fixture();
  const path = buildMovePath(action, entry, device);
  for (let i = 0; i <= 100; i++) {
    const sample = sampleMovePath(path, i / 100);
    const a = sample.atoms.get(0), b = sample.atoms.get(1), grid = sample.grids.get('a');
    assert.ok(Math.abs(b.x - a.x - 5) < 1e-10);
    assert.equal(b.y, a.y);
    assert.equal(a.x, grid.columns[0].coordinate);
    assert.equal(a.y, grid.rows[0].coordinate);
  }
});

test('lane model retains exact boundaries, own duration, reverse seeks and input', () => {
  const { trace } = fixture(), original = JSON.stringify(trace);
  const lanes = new Replay(trace, { motion: 'lanes' }), direct = new Replay(trace);
  assert.deepEqual(lanes.frame(0, 0).atoms, direct.frame(0, 0).atoms);
  assert.deepEqual(lanes.frame(0, 0.5).atoms, direct.frame(0, 0.5).atoms);
  assert.deepEqual(lanes.frame(0, 1).grids, direct.frame(0, 1).grids);
  assert.equal(lanes.frame(0, 0.2).time_us, 4);
  assert.deepEqual(lanes.frame(0, 0).grids, direct.frame(0, 0).grids);
  assert.equal(JSON.stringify(trace), original);
});

test('offset reverses sign at workspace edge; no room is an explicit fallback', () => {
  const { action, entry, device } = fixture();
  device.aods[0].workspace = [-10, -10, 40, 20];
  let path = buildMovePath(action, entry, device);
  assert.equal(path.mode, 'lanes');
  assert.ok(path.atoms.get(0)[1].y < 0);
  device.aods[0].workspace = [0, 0, 15, 20];
  path = buildMovePath(action, entry, device);
  assert.equal(path.mode, 'direct');
  assert.match(path.reason, /offset/);
});

test('flexible grid uses stable line IDs and one shared axis phase', () => {
  const { action, entry, device } = fixture();
  action.patch.atoms[1].x = 17;
  action.patch.grids[0].columns = [{ id: 1, coordinate: 17 }, { id: 0, coordinate: 10 }];
  const path = buildMovePath(action, entry, device);
  for (let i = 0; i <= 100; i++) {
    const sample = sampleMovePath(path, i / 100);
    const columns = new Map(sample.grids.get('a').columns.map(line => [line.id, line.coordinate]));
    assert.equal(sample.atoms.get(0).x, columns.get(0));
    assert.equal(sample.atoms.get(1).x, columns.get(1));
    assert.ok(columns.get(1) > columns.get(0));
  }
});

test('empty and zero-distance movement stay still', () => {
  const { action, entry, device } = fixture();
  action.patch.atoms = [...entry.atoms.values()];
  action.patch.grids = [...entry.grids.values()];
  const path = buildMovePath(action, entry, device);
  assert.deepEqual(sampleMovePath(path, 0.5).atoms.get(0), { x: 0, y: 0 });
  action.patch = { atoms: [], grids: [] };
  assert.equal(sampleMovePath(buildMovePath(action, entry, device), 0.5).atoms.size, 0);
});

test('sparse unchanged carried atoms follow their lines and return at completion', () => {
  const { trace, action } = fixture();
  action.patch.atoms = [action.patch.atoms[1]];
  action.patch.atoms[0].y = 0;
  action.patch.grids[0].rows[0].coordinate = 0;
  action.patch.grids[0].columns[0].coordinate = 0;
  const replay = new Replay(trace, { motion: 'lanes' });
  const frame = replay.frame(0, 0.25);
  assert.equal(frame.atoms.get(0).x, frame.grids.get('a').columns[0].coordinate);
  assert.equal(frame.atoms.get(0).y, frame.grids.get('a').rows[0].coordinate);
  assert.notEqual(frame.atoms.get(0).y, 0);
  assert.equal(replay.frame(0, 0.5).atoms.get(0).y, 0);
  assert.equal(replay.frame(0, 0).atoms.get(0).x, 0);
});

test('axis lanes avoid stationary lattice points throughout the example path', () => {
  const { action, entry, device } = fixture();
  const obstacles = [{ id: 8, x: 0, y: 10, holder: { kind: 'slm' } }, { id: 9, x: 5, y: 10, holder: { kind: 'slm' } }];
  for (const atom of obstacles) entry.atoms.set(atom.id, atom);
  const path = buildMovePath(action, entry, device);
  for (const points of path.atoms.values()) {
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i], dx = b.x - a.x, dy = b.y - a.y;
      for (const p of obstacles) {
        const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
        assert.ok(Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy) > 1e-9);
      }
    }
  }
});
