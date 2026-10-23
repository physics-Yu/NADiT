/* Viewer interaction tests with a DOM/canvas adapter, not browser rendering. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assets = path.resolve(__dirname, '../../src/nadit_physical_compiler/visualization/web');

function viewer({native = false, reject = false, height = 600, withAtoms = false} = {}) {
  const nodes = new Map(), timers = new Map(), frames = new Map(), rectangles = [];
  const circles = [], texts = [], resizeObservers = [], loadedTraces = [];
  let serial = 0;
  const context = new Proxy({fillRect(...args) { rectangles.push([this.fillStyle, ...args]); },
    arc(x, y, radius) { circles.push({x, y, radius, lineWidth: this.lineWidth}); },
    fillText(text) { texts.push({text, font: this.font}); }},
    {get: (obj, key) => obj[key] ?? (() => {})});
  const document = {handlers: {}, fullscreenElement: null, activeElement: null,
    addEventListener(name, fn) { this.handlers[name] = fn; }};
  function element(id, tag = 'DIV') {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {id, tagName: tag, value: '', textContent: '', hidden: false,
        checked: false, style: {}, dataset: {}, handlers: {}, attributes: {}, children: [],
        classList: {add: c => classes.add(c), remove: c => classes.delete(c),
          contains: c => classes.has(c), toggle(c, on) { if (on) classes.add(c); else classes.delete(c); }},
        addEventListener(name, fn) { this.handlers[name] = fn; },
        setAttribute(k, v) { this.attributes[k] = v; },
        append(...children) { this.children.push(...children); },
        replaceChildren() { this.children = []; },
        focus() { document.activeElement = this; },
        click() { return this.handlers.click?.({target: this}); },
        getContext() { return context; },
        getBoundingClientRect() { return {left: 0, top: 0, width: 800, height: id === 'step-strip' ? 12 : height}; },
        parentElement: {clientWidth: 800}, setPointerCapture() {}});
    }
    return nodes.get(id);
  }
  const html = fs.readFileSync(path.join(assets, 'viewer.html'), 'utf8');
  for (const match of html.matchAll(/<([\w-]+)[^>]*\bid="([^"]+)"/g)) element(match[2], match[1].toUpperCase());
  document.getElementById = id => nodes.get(id) || null;
  document.createElement = tag => element('new-' + serial++, tag.toUpperCase());
  document.body = element('body');
  const action = kind => ({kind, index: 0, duration_us: 10, text: kind, atoms: [], pairs: [], source_ids: [], patch: {atoms: []}});
  const trace = {title: 'Example', description: '', atom_ir: '', issues: [], duration_us: 45, program_duration_us: 40,
    device: {name: 'device', zones: [{id: 'storage', bounds: [0, 0, 20, 20], operations: ['load', 'store']}], slm_sites: [], aods: []},
    initial: {atoms: []}, steps: ['Load', 'Move', 'Store', 'CZPulse'].map((kind, i) => ({start_us: i * 10, duration_us: 10, actions: [action(kind)]}))};
  if (withAtoms) {
    trace.initial.atoms = [{id: 0, qubit: 0, x: 5, y: 5, holder: {kind: 'slm', site_id: 's'}}];
    trace.device.slm_sites = [{id: 's', x: 5, y: 5}];
    trace.initial.grids = [{aod_id: 'a', rows: [{id: 0, coordinate: 6}], columns: [{id: 0, coordinate: 6}]}];
    trace.device.aods = [{id: 'a', workspace: [0, 0, 20, 20]}];
    element('sites-toggle').checked = true;
    element('grid-toggle').checked = true;
    element('labels-toggle').checked = true;
  }
  element('atom-replay-data').textContent = JSON.stringify({traces: [trace, {...trace, steps: [], duration_us: 0, program_duration_us: 0}]});
  for (const [id, value] of Object.entries({'path-select': 'direct', 'speed-select': '1', 'timing-select': 'step'})) element(id).value = value;
  class Replay {
    constructor(trace) { this.trace = trace; this.motion = 'direct'; loadedTraces.push(trace); }
    frame(step, fraction) {
      const s = this.trace.steps[step];
      return {actions: s?.actions || [], time_us: s ? s.start_us + fraction * 10 : this.trace.program_duration_us + fraction * 5,
        elapsed_us: fraction * 10, fraction, atoms: new Map(this.trace.initial.atoms.map(a => [a.id, a])), grids: new Map((this.trace.initial.grids || []).map(g => [g.aod_id, g])), measurements: [], movePaths: new Map()};
    }
  }
  const sandbox = {document, window: {devicePixelRatio: 1},
    AtomReplayModel: {Replay, actionProgress: () => 0},
    AtomReplayAnalysis: {Analysis: class {}},
    AtomReplayDashboard: {Dashboard: class {load() {} update() {return null;}}},
    ResizeObserver: class {constructor(fn) {resizeObservers.push(fn);} observe() {}},
    requestAnimationFrame(fn) { const id = ++serial; frames.set(id, fn); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(fn) {const id = ++serial; timers.set(id, fn); return id;},
    clearTimeout(id) {timers.delete(id);}};
  if (native) element('stage').requestFullscreen = async () => {
    if (reject) throw Error('Not allowed');
    document.fullscreenElement = element('stage'); document.handlers.fullscreenchange();
  };
  document.exitFullscreen = async () => { document.fullscreenElement = null; document.handlers.fullscreenchange(); };
  vm.runInNewContext(fs.readFileSync(path.join(assets, 'viewer.js'), 'utf8'), sandbox);
  return {nodes, element, document, rectangles, frames, timers, circles, texts, loadedTraces,
    resize(nextHeight) {height = nextHeight; resizeObservers.forEach(fn => fn()); element('fit-button').click();},
    key(key, target = element('atom-canvas')) { document.handlers.keydown({key, code: key === ' ' ? 'Space' : key, target, preventDefault() {}}); },
    flush() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(0)); }};
}

test('step strip distinguishes transfers, previews CZ and feedback tail without seeking', () => {
  const v = viewer(), slider = v.element('step-slider');
  assert.match(v.element('step-preview').textContent, /Load.*SLM.*AOD/);
  slider.handlers.pointermove({clientX: 600});
  assert.match(v.element('step-preview').textContent, /Step 4.*CZ.*30.*40/);
  assert.equal(Number(slider.value), 0);
  slider.handlers.pointermove({clientX: 800});
  assert.match(v.element('step-preview').textContent, /feedback|measurement results/i);
  slider.value = 3; slider.handlers.input({target: slider});
  assert.match(slider.attributes['aria-valuetext'], /CZ/);
  assert.match(v.element('hud-kind').textContent, /CZ/);
  const colors = v.rectangles.filter(r => r[4] === 12).map(r => r[0]);
  assert.ok(new Set(colors).size >= 4, 'Load, Move, Store and CZ have distinct colours');
});

test('atoms, SLM sites and AOD spots scale with scene geometry while labels remain fixed', async () => {
  const v = viewer({withAtoms: true}), canvas = v.element('atom-canvas');
  const sizes = () => ({
    marks: v.circles.slice(-3).map(({radius}) => radius),
    zoneWidth: v.rectangles.findLast(r => r[0] === '#24374802')[3],
    atomFont: v.texts.findLast(t => t.text === '0').font,
    zoneFont: v.texts.findLast(t => t.text === 'storage').font
  });
  const original = sizes(), initialPosition = v.circles.at(-1).x;
  function proportional() {
    const current = sizes(), ratio = current.zoneWidth / original.zoneWidth;
    current.marks.forEach((radius, i) => assert.ok(Math.abs(radius / original.marks[i] - ratio) < 1e-9));
    assert.equal(current.atomFont, original.atomFont);
    assert.equal(current.zoneFont, original.zoneFont);
  }
  canvas.handlers.wheel({deltaY: 1000, clientX: 400, clientY: 300, preventDefault() {}});
  proportional();
  assert.ok(sizes().marks.every((radius, i) => radius < original.marks[i]));
  assert.notEqual(v.circles.at(-1).x, initialPosition, 'world positions must still zoom');
  canvas.handlers.wheel({deltaY: -1000, clientX: 400, clientY: 300, preventDefault() {}});
  proportional();
  v.resize(300);
  proportional();
  await v.element('present-button').click(); v.flush();
  proportional();
  v.key('Escape'); v.flush();
  proportional();
});

test('presentation fallback preserves playback and Escape works from an input', async () => {
  const v = viewer();
  v.element('play-button').click();
  await v.element('present-button').click(); v.flush();
  assert.ok(v.element('stage').classList.contains('is-presenting'));
  assert.equal(v.element('play-button').textContent, 'Pause');
  assert.equal(v.element('present-button').attributes['aria-pressed'], 'true');
  v.key('Escape', v.element('time-input')); v.flush();
  assert.ok(!v.element('stage').classList.contains('is-presenting'));
  assert.equal(v.element('play-button').textContent, 'Pause');
});

test('size controls independently change display radii and reset without editing the trace', () => {
  const v = viewer({withAtoms: true});
  const radii = () => v.circles.slice(-3).map(c => c.radius);
  const initial = radii(), data = JSON.stringify(v.loadedTraces[0]);
  const adjust = (kind, value) => {
    const slider = v.element(kind + '-size'); slider.value = String(value);
    slider.handlers.input({target: slider});
  };
  v.element('play-button').click();
  adjust('atom', 2);
  assert.deepEqual(radii(), [initial[0], initial[1], initial[2] * 2]);
  adjust('site', .5);
  assert.deepEqual(radii(), [initial[0] * .5, initial[1], initial[2] * 2]);
  adjust('aod', 3);
  assert.deepEqual(radii(), [initial[0] * .5, initial[1] * 3, initial[2] * 2]);
  assert.equal(v.element('atom-size-value').textContent, '200%');
  assert.equal(v.element('site-size-value').textContent, '50%');
  assert.equal(v.element('aod-size').attributes['aria-valuetext'], '300%');
  assert.equal(v.element('play-button').textContent, 'Pause');
  assert.equal(Number(v.element('time-input').value), 0);
  v.element('reset-sizes').click();
  assert.deepEqual(radii(), initial);
  for (const kind of ['atom', 'site', 'aod']) {
    assert.equal(v.element(kind + '-size').value, '1');
    assert.equal(v.element(kind + '-size-value').textContent, '100%');
  }
  assert.equal(JSON.stringify(v.loadedTraces[0]), data);
});

test('custom sizes persist across traces and fullscreen and still scale with zoom', async () => {
  const v = viewer({withAtoms: true});
  const slider = v.element('atom-size'); slider.value = '1.5'; slider.handlers.input({target: slider});
  v.element('trace-select').handlers.change({target: {value: '1'}});
  v.element('trace-select').handlers.change({target: {value: '0'}});
  assert.equal(slider.value, '1.5');
  await v.element('present-button').click(); v.flush();
  const before = v.circles.slice(-3).map(c => c.radius);
  const width = () => v.rectangles.findLast(r => r[0] === '#24374802')[3];
  const previousWidth = width();
  v.element('atom-canvas').handlers.wheel({deltaY: 100, clientX: 400, clientY: 300, preventDefault() {}});
  v.circles.slice(-3).forEach((c, i) => assert.ok(Math.abs(c.radius / before[i] - width() / previousWidth) < 1e-9));
  v.element('site-size').value = '.25';
  v.element('site-size').handlers.input({target: v.element('site-size')});
  assert.equal(v.element('site-size-value').textContent, '25%');
  assert.equal(slider.value, '1.5');
});

test('native fullscreen exit and rejected requests restore usable controls', async () => {
  const v = viewer({native: true});
  await v.element('present-button').click(); v.flush();
  assert.equal(v.document.fullscreenElement, v.element('stage'));
  await v.document.exitFullscreen(); v.flush();
  assert.ok(!v.element('stage').classList.contains('is-presenting'));
  assert.equal(v.element('present-button').attributes['aria-pressed'], 'false');
  const blocked = viewer({native: true, reject: true});
  await blocked.element('present-button').click(); blocked.flush();
  assert.ok(blocked.element('stage').classList.contains('is-presenting'));
  blocked.key('Escape'); blocked.flush();
  assert.ok(!blocked.element('stage').classList.contains('is-presenting'));
});

test('idle controls wake, empty traces and short presentation viewports remain usable', async () => {
  const v = viewer({height: 200});
  await v.element('present-button').click(); v.flush();
  v.element('play-button').click();
  for (const fn of v.timers.values()) fn();
  assert.ok(v.element('stage').classList.contains('is-idle'));
  v.element('stage').handlers.pointermove();
  assert.ok(!v.element('stage').classList.contains('is-idle'));
  v.element('trace-select').handlers.change({target: {value: '1'}});
  assert.match(v.element('step-preview').textContent, /Final layout/);
  assert.ok(v.rectangles.every(r => r.slice(1).every(Number.isFinite)));
});
