(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const data = JSON.parse($('atom-replay-data').textContent);
  const canvas = $('atom-canvas');
  const context = canvas.getContext('2d');
  const colors = { slm: '#4772b1', aod: '#147b7b', pulse: '#9b65a8', measure: '#b96c25', control: '#c58b35' };
  const numeric = (value, digits = 2) => Number(value).toLocaleString('en-US', { maximumFractionDigits: digits });
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const camera = { x: 0, y: 0, scale: 1, width: 1, height: 1 };
  let trace, replay, frame, cursor = { step: 0, fraction: 0 };
  let playing = false, previousTimestamp = null, animationId = null, selectedAtom = null;
  let currentStep = -1, currentMeasurementCount = -1, selectedIssue = null;
  let actionCards = [], drag = null;
  let analysis, risk;
  const dashboard = new AtomReplayDashboard.Dashboard(
    time => { const target = replay.cursorAtTime(time); seek(target.step, target.fraction); },
    atom => { selectedAtom = atom; render(); }
  );

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function toScreen(x, y) { return [(x - camera.x) * camera.scale + camera.width / 2, camera.height / 2 - (y - camera.y) * camera.scale]; }
  function toWorld(x, y) { return [(x - camera.width / 2) / camera.scale + camera.x, (camera.height / 2 - y) / camera.scale + camera.y]; }
  function line(x1, y1, x2, y2) {
    const start = toScreen(x1, y1), end = toScreen(x2, y2);
    context.beginPath(); context.moveTo(...start); context.lineTo(...end); context.stroke();
  }
  function circle(x, y, radius, fill, stroke) {
    const point = toScreen(x, y);
    context.beginPath(); context.arc(point[0], point[1], radius, 0, Math.PI * 2);
    if (fill) { context.fillStyle = fill; context.fill(); }
    if (stroke) { context.strokeStyle = stroke; context.stroke(); }
  }
  function rectangle(bounds, fill, stroke) {
    const top = toScreen(bounds[0], bounds[3]);
    const width = (bounds[2] - bounds[0]) * camera.scale;
    const height = (bounds[3] - bounds[1]) * camera.scale;
    if (fill) { context.fillStyle = fill; context.fillRect(top[0], top[1], width, height); }
    if (stroke) { context.strokeStyle = stroke; context.strokeRect(top[0], top[1], width, height); }
  }
  function worldBounds() {
    const extent = [Infinity, Infinity, -Infinity, -Infinity];
    function include(x, y) {
      extent[0] = Math.min(extent[0], x); extent[1] = Math.min(extent[1], y);
      extent[2] = Math.max(extent[2], x); extent[3] = Math.max(extent[3], y);
    }
    function includeBounds(bounds) { include(bounds[0], bounds[1]); include(bounds[2], bounds[3]); }
    trace.device.zones.forEach(zone => includeBounds(zone.bounds));
    trace.device.slm_sites.forEach(site => include(site.x, site.y));
    trace.initial.atoms.forEach(atom => include(atom.x, atom.y));
    for (const step of trace.steps) {
      for (const action of step.actions) {
        if (action.kind === 'Move') action.patch.atoms.forEach(atom => include(atom.x, atom.y));
      }
    }
    if (!Number.isFinite(extent[0])) trace.device.aods.forEach(aod => includeBounds(aod.workspace));
    return Number.isFinite(extent[0]) ? extent : [-10, -10, 10, 10];
  }
  function fitView() {
    const bounds = worldBounds();
    camera.x = (bounds[0] + bounds[2]) / 2;
    camera.y = (bounds[1] + bounds[3]) / 2;
    camera.scale = Math.min((camera.width - 100) / Math.max(10, bounds[2] - bounds[0]), (camera.height - 90) / Math.max(10, bounds[3] - bounds[1]));
    render();
  }
  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    camera.width = rect.width; camera.height = rect.height;
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    render();
  }
  function drawAxes() {
    const target = 75 / camera.scale;
    const exponent = Math.pow(10, Math.floor(Math.log10(target)));
    const interval = [1, 2, 5, 10].find(value => value * exponent >= target) * exponent;
    const bottomLeft = toWorld(0, camera.height), topRight = toWorld(camera.width, 0);
    context.strokeStyle = '#eaf0f2'; context.lineWidth = 1;
    context.fillStyle = '#a0adb5'; context.font = '9px Segoe UI, sans-serif';
    for (let x = Math.ceil(bottomLeft[0] / interval) * interval; x <= topRight[0]; x += interval) {
      line(x, bottomLeft[1], x, topRight[1]);
      context.fillText(numeric(x, 2), toScreen(x, 0)[0] + 4, 13);
    }
    for (let y = Math.ceil(bottomLeft[1] / interval) * interval; y <= topRight[1]; y += interval) {
      line(bottomLeft[0], y, topRight[0], y);
      context.fillText(numeric(y, 2), 6, toScreen(0, y)[1] - 5);
    }
  }
  function drawZones() {
    for (const zone of trace.device.zones) {
      const operations = zone.operations.join(' ').toLowerCase();
      const fill = operations.includes('cz') ? '#f4edf94f' : operations.includes('measure') ? '#fbf3e34f' : '#edf3fa65';
      context.lineWidth = 1; context.setLineDash([4, 4]);
      rectangle(zone.bounds, fill, '#bccbd277');
      context.setLineDash([]);
      const point = toScreen(zone.bounds[0], zone.bounds[3]);
      context.fillStyle = '#8d9ca8'; context.font = '10px Segoe UI, sans-serif';
      context.fillText(zone.id, point[0] + 6, point[1] + 15);
    }
    if ($('sites-toggle').checked) {
      context.lineWidth = .8;
      for (const site of trace.device.slm_sites) circle(site.x, site.y, clamp(camera.scale * .45, 1.1, 3.6), null, '#a8b9ca80');
    }
  }
  function drawGrids() {
    if (!$('grid-toggle').checked) return;
    for (const grid of frame.grids.values()) {
      const device = trace.device.aods.find(aod => aod.id === grid.aod_id);
      const bounds = device ? device.workspace : worldBounds();
      context.strokeStyle = '#24958d80'; context.lineWidth = 1;
      for (const row of grid.rows) line(bounds[0], row.coordinate, bounds[2], row.coordinate);
      for (const column of grid.columns) line(column.coordinate, bounds[1], column.coordinate, bounds[3]);
      for (const row of grid.rows) {
        for (const column of grid.columns) circle(column.coordinate, row.coordinate, 1.5, '#35a29c80');
      }
      if ($('labels-toggle').checked) {
        context.font = '9px Consolas, monospace'; context.fillStyle = '#4d9e99';
        for (const row of grid.rows) { const p = toScreen(bounds[0], row.coordinate); context.fillText('r' + row.id, p[0] + 3, p[1] - 3); }
        for (const column of grid.columns) { const p = toScreen(column.coordinate, bounds[3]); context.fillText('c' + column.id, p[0] + 3, p[1] - 4); }
      }
    }
  }
  function drawMoves() {
    if (!$('trails-toggle').checked) return;
    for (const action of frame.actions) {
      if (action.kind !== 'Move') continue;
      context.strokeStyle = '#439b9880'; context.lineWidth = 1.3; context.setLineDash([4, 4]);
      for (const points of frame.movePaths.get(action).atoms.values()) {
        const destination = points[points.length - 1];
        for (let i = 1; i < points.length; i++) line(points[i - 1].x, points[i - 1].y, points[i].x, points[i].y);
        circle(destination.x, destination.y, 4, null, '#439b9880');
      }
      context.setLineDash([]);
    }
  }
  function drawPulses() {
    for (const action of frame.actions) {
      if (!action.coverage || actionProgress(action) === 1) continue;
      const color = action.kind === 'CZPulse' ? colors.pulse : colors.control;
      context.lineWidth = 1.5;
      for (const bounds of action.coverage.regions) rectangle(bounds, color + '16', color + '70');
      for (const pair of action.pairs) {
        const a = frame.atoms.get(pair[0]), b = frame.atoms.get(pair[1]);
        if (!a || !b) continue;
        context.strokeStyle = color; context.lineWidth = 2.5; line(a.x, a.y, b.x, b.y);
      }
    }
  }
  function actionHighlights() {
    const highlights = new Map();
    for (const action of frame.actions) {
      if (actionProgress(action) === 1) continue;
      let color;
      if (action.kind === 'CZPulse') color = colors.pulse;
      else if (action.kind === 'Measure') color = colors.measure;
      else if (['Rotation', 'LocalU', 'GlobalRY', 'Reset'].includes(action.kind)) color = colors.control;
      else continue;
      for (const atomId of action.atoms) highlights.set(atomId, color);
    }
    return highlights;
  }
  function drawAtoms() {
    const highlights = actionHighlights();
    const radius = clamp(camera.scale * .7, 3.1, 6.5);
    for (const atom of frame.atoms.values()) {
      const point = toScreen(atom.x, atom.y);
      if (point[0] < -30 || point[0] > camera.width + 30 || point[1] < -30 || point[1] > camera.height + 30) continue;
      if (highlights.has(atom.id)) circle(atom.x, atom.y, radius + 4, highlights.get(atom.id) + '24', highlights.get(atom.id) + 'aa');
      const fill = $('risk-toggle').checked && risk ? `hsl(${175 - 155 * (risk.atoms.get(atom.id) / analysis.maxAtomLoss)} 48% 47%)` : atom.holder.kind === 'aod' ? colors.aod : colors.slm;
      context.lineWidth = 1.2; circle(atom.x, atom.y, radius, fill, '#ffffff');
      if (risk?.maximum.atom === atom.id) {
        context.lineWidth = 2; circle(atom.x, atom.y, radius + 9, null, '#b25138');
        context.fillStyle = '#a3432f'; context.font = 'bold 10px Segoe UI, sans-serif';
        context.fillText('MAX · ' + atom.id, point[0] + radius + 12, point[1] + 13);
      }
      if (atom.id === selectedAtom) { context.lineWidth = 1.6; circle(atom.x, atom.y, radius + 6, null, '#143c52'); }
      if ($('labels-toggle').checked || atom.id === selectedAtom) {
        context.fillStyle = '#3b576c'; context.font = '10px Consolas, monospace';
        context.fillText(String(atom.id), point[0] + radius + 4, point[1] - radius - 2);
      }
    }
    if (selectedIssue) {
      context.lineWidth = 1.5; context.strokeStyle = '#bb7430';
      circle(selectedIssue.x, selectedIssue.y, radius + 11, null, '#bb7430');
    }
  }
  function drawScale() {
    const target = 90 / camera.scale;
    const exponent = Math.pow(10, Math.floor(Math.log10(target)));
    const distance = ([5, 2, 1].find(value => value * exponent <= target) || 1) * exponent;
    const width = distance * camera.scale;
    context.strokeStyle = '#758e9b'; context.lineWidth = 1.2; context.beginPath();
    context.moveTo(camera.width - width - 20, camera.height - 40); context.lineTo(camera.width - 20, camera.height - 40);
    context.stroke(); context.font = '9px Segoe UI, sans-serif'; context.fillStyle = '#718894';
    context.fillText(numeric(distance) + ' µm', camera.width - width - 20, camera.height - 47);
  }
  function draw() {
    context.clearRect(0, 0, camera.width, camera.height);
    drawAxes(); drawZones(); drawMoves(); drawGrids(); drawPulses(); drawAtoms(); drawScale();
    const fallback = [...frame.movePaths.values()].some(path => path.reason);
    $('path-note').textContent = replay.motion === 'direct' ? 'Direct preview · recorded timing' : fallback ? 'No display lane · direct preview' : 'Lane preview · recorded timing';
  }
  function buildActionList() {
    const container = $('action-list'); container.replaceChildren(); actionCards = [];
    $('action-count').textContent = frame.actions.length + ' actions';
    if (!frame.actions.length) {
      container.append(element('p', 'empty-state', frame.time_us < trace.duration_us ? 'Waiting for measurement availability.' : 'Program complete. Final atom layout shown.'));
      return;
    }
    for (const action of frame.actions) {
      const card = element('article', 'action-card');
      const heading = element('div', 'action-title');
      heading.append(element('strong', null, action.kind), element('span', null, numeric(action.duration_us) + ' µs'));
      card.append(heading, element('pre', null, action.text));
      const details = ['Action ' + action.index, action.source_ids.length ? 'source ' + action.source_ids.join(', ') : 'no source ID'];
      if (action.pairs.length) details.push(action.pairs.length + ' CZ pairs');
      card.append(element('div', 'action-meta', details.join(' · ')));
      const progress = element('div', 'action-progress'), bar = element('i'); progress.append(bar); card.append(progress);
      container.append(card); actionCards.push({ card, bar, action });
    }
  }
  function updateInspector() {
    const atom = frame.atoms.get(selectedAtom);
    const inspector = $('atom-inspector'); inspector.replaceChildren();
    $('selection-chip').hidden = !atom;
    $('selected-atom-label').textContent = atom ? 'Atom ' + atom.id : 'No selection';
    if (!atom) { inspector.className = 'empty-state'; inspector.textContent = 'Select an atom in the layout to inspect its identity, position and trap.'; return; }
    $('selection-chip').textContent = 'Atom ' + atom.id + ' · ' + atom.holder.kind.toUpperCase();
    inspector.className = 'detail-grid';
    const holder = atom.holder;
    const details = [['ATOM ID', atom.id], ['PHYSICAL QUBIT', atom.qubit === null ? '—' : atom.qubit], ['X · µm', numeric(atom.x, 4)], ['Y · µm', numeric(atom.y, 4)], ['HOLDER', holder.kind === 'slm' ? 'SLM · ' + holder.site_id : 'AOD · ' + holder.aod_id], ...(holder.kind === 'aod' ? [['ROW / COLUMN', holder.row + ' / ' + holder.column]] : [])];
    for (const [label, value] of details) { const cell = element('div'); cell.append(element('small', null, label), element('strong', null, String(value))); inspector.append(cell); }
  }
  function updateMeasurements() {
    if (currentMeasurementCount === frame.measurements.length) return;
    currentMeasurementCount = frame.measurements.length;
    $('measurement-count').textContent = frame.measurements.length + ' available';
    const list = $('measurement-list'); list.replaceChildren();
    if (!frame.measurements.length) list.append(element('p', 'empty-state', 'No measurement result is available at this time.'));
    for (const record of frame.measurements) {
      const row = element('div', 'measurement-row');
      row.append(element('span', null, 'm' + record.measurement_id), element('strong', null, String(record.value)), element('small', null, numeric(record.available_at_us) + ' µs'));
      list.append(row);
    }
  }
  function updateControls() {
    $('step-slider').value = cursor.step;
    $('fraction-slider').value = Math.round(cursor.fraction * 1000);
    $('fraction-value').textContent = numeric(cursor.fraction * 100, 1) + '%';
    if (document.activeElement !== $('time-input')) $('time-input').value = Number(frame.time_us.toFixed(6));
    $('step-position').textContent = cursor.step < trace.steps.length ? (cursor.step + 1) + ' / ' + trace.steps.length : 'End / ' + trace.steps.length;
    $('step-kind').textContent = cursor.step < trace.steps.length ? [...new Set(frame.actions.map(action => action.kind))].join(' + ') : frame.time_us < trace.duration_us ? 'Measurement availability' : 'Final layout';
    $('previous-step').disabled = cursor.step === 0 && cursor.fraction === 0;
    $('next-step').disabled = cursor.step === trace.steps.length && cursor.fraction === 1;
    for (const { card, bar, action } of actionCards) {
      const fraction = actionProgress(action);
      card.classList.toggle('active', fraction < 1); card.classList.toggle('complete', fraction === 1);
      bar.style.width = fraction * 100 + '%';
    }
  }
  function actionProgress(action) {
    return AtomReplayModel.actionProgress(action.duration_us, trace.steps[cursor.step].duration_us, frame.elapsed_us, frame.fraction);
  }
  function render() {
    if (!replay) return;
    frame = replay.frame(cursor.step, cursor.fraction);
    if (currentStep !== cursor.step) { currentStep = cursor.step; buildActionList(); }
    risk = dashboard.update(frame.time_us, selectedAtom, cursor.step);
    updateControls(); updateInspector(); updateMeasurements(); draw();
  }
  function setPlaying(value) {
    if (animationId !== null) cancelAnimationFrame(animationId);
    animationId = null;
    playing = value; previousTimestamp = null;
    $('play-button').textContent = playing ? 'Ⅱ Pause' : '▶ Play';
    $('play-button').setAttribute('aria-label', playing ? 'Pause' : 'Play');
    if (playing) animationId = requestAnimationFrame(animate);
  }
  function seek(step, fraction = 0) {
    setPlaying(false); selectedIssue = null;
    cursor = { step: clamp(step, 0, trace.steps.length), fraction: clamp(fraction, 0, 1) };
    render();
  }
  function animate(timestamp) {
    if (!playing) return;
    const elapsed = previousTimestamp === null ? 0 : Math.min(timestamp - previousTimestamp, 100);
    previousTimestamp = timestamp;
    const speed = Number($('speed-select').value);
    if ($('timing-select').value === 'proportional' && trace.duration_us > 0) {
      const time = Math.min(trace.duration_us, frame.time_us + elapsed * speed * trace.duration_us / 30000);
      cursor = replay.cursorAtTime(time);
    } else {
      cursor.fraction += elapsed * speed / 800;
      while (cursor.fraction >= 1 && cursor.step < trace.steps.length) {
        cursor.fraction -= 1; cursor.step += 1;
      }
      cursor.fraction = Math.min(cursor.fraction, 1);
    }
    render();
    if (cursor.step === trace.steps.length && (cursor.fraction >= 1 || trace.duration_us === trace.program_duration_us)) setPlaying(false);
    else animationId = requestAnimationFrame(animate);
  }
  function buildIssues() {
    const list = $('issue-list'); list.replaceChildren();
    $('issue-count').textContent = trace.issues.length;
    if (!trace.issues.length) { list.append(element('p', 'empty-state', 'No occupied-intersection observations in this trace.')); return; }
    for (const issue of trace.issues) {
      const button = element('button', 'issue-row');
      button.append(element('strong', null, 'Atom ' + issue.atom_id + ' · ' + (issue.step_index < 0 ? 'initial layout' : 'after step ' + (issue.step_index + 1))), element('span', null, numeric(issue.time_us) + ' µs · ' + issue.message));
      button.addEventListener('click', () => {
        seek(issue.step_index < 0 ? 0 : issue.step_index, issue.step_index < 0 ? 0 : 1);
        selectedIssue = issue; selectedAtom = issue.atom_id;
        camera.x = issue.x; camera.y = issue.y; render();
      });
      list.append(button);
    }
  }
  function loadTrace(index) {
    setPlaying(false); trace = data.traces[index]; replay = new AtomReplayModel.Replay(trace, { motion: $('path-select').value });
    analysis = new AtomReplayAnalysis.Analysis(trace); dashboard.load(trace, analysis);
    cursor = { step: 0, fraction: 0 }; selectedAtom = null; selectedIssue = null; currentStep = -1; currentMeasurementCount = -1;
    $('trace-title').textContent = trace.title; $('trace-description').textContent = trace.description;
    $('atom-count').textContent = trace.initial.atoms.length; $('step-count').textContent = trace.steps.length;
    $('duration-total').textContent = numeric(trace.duration_us); $('device-name').textContent = trace.device.name;
    $('step-slider').max = trace.steps.length; $('time-input').max = trace.duration_us;
    $('ir-source').textContent = trace.atom_ir; buildIssues(); resize(); fitView();
  }
  data.traces.forEach((record, index) => { const option = element('option', null, record.title); option.value = index; $('trace-select').append(option); });
  $('trace-select').addEventListener('change', event => loadTrace(Number(event.target.value)));
  $('path-select').addEventListener('change', event => {
    replay = new AtomReplayModel.Replay(trace, { motion: event.target.value });
    render();
  });
  $('play-button').addEventListener('click', () => {
    if (!playing && cursor.step === trace.steps.length && (cursor.fraction === 1 || trace.duration_us === trace.program_duration_us)) seek(0, 0);
    setPlaying(!playing);
  });
  $('previous-step').addEventListener('click', () => seek(cursor.fraction > 0 ? cursor.step : cursor.step - 1));
  $('next-step').addEventListener('click', () => seek(cursor.step < trace.steps.length ? cursor.step + 1 : cursor.step, cursor.step >= trace.steps.length ? 1 : 0));
  $('step-slider').addEventListener('input', event => seek(Number(event.target.value)));
  $('fraction-slider').addEventListener('input', event => seek(cursor.step, Number(event.target.value) / 1000));
  function commitTime() {
    const time = Number($('time-input').value);
    if (Number.isFinite(time)) { const next = replay.cursorAtTime(time); seek(next.step, next.fraction); }
    $('time-input').value = Number(frame.time_us.toFixed(6));
  }
  $('time-input').addEventListener('change', commitTime);
  $('time-input').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); commitTime(); }
  });
  $('fit-button').addEventListener('click', fitView);
  for (const id of ['sites-toggle', 'grid-toggle', 'labels-toggle', 'trails-toggle', 'risk-toggle']) $(id).addEventListener('change', render);
  function toggleIR(open) { $('ir-panel').hidden = !open; $('ir-toggle').setAttribute('aria-expanded', String(open)); }
  $('ir-toggle').addEventListener('click', () => toggleIR($('ir-panel').hidden));
  $('ir-close').addEventListener('click', () => toggleIR(false));
  canvas.addEventListener('wheel', event => {
    event.preventDefault(); const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top, before = toWorld(x, y);
    camera.scale = clamp(camera.scale * Math.exp(-event.deltaY * .0015), .03, 500);
    const after = toWorld(x, y); camera.x += before[0] - after[0]; camera.y += before[1] - after[1]; render();
  }, { passive: false });
  canvas.addEventListener('pointerdown', event => { canvas.setPointerCapture(event.pointerId); drag = { x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY }; });
  canvas.addEventListener('pointermove', event => {
    const rect = canvas.getBoundingClientRect(), world = toWorld(event.clientX - rect.left, event.clientY - rect.top);
    $('view-position').textContent = 'x ' + numeric(world[0]) + ' / y ' + numeric(world[1]) + ' µm';
    if (!drag) return;
    camera.x -= (event.clientX - drag.lastX) / camera.scale; camera.y += (event.clientY - drag.lastY) / camera.scale;
    drag.lastX = event.clientX; drag.lastY = event.clientY; render();
  });
  canvas.addEventListener('pointerup', event => {
    if (!drag) return;
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) {
      const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
      let nearest = null, distance = 14;
      for (const atom of frame.atoms.values()) { const p = toScreen(atom.x, atom.y), d = Math.hypot(x - p[0], y - p[1]); if (d < distance) { nearest = atom.id; distance = d; } }
      selectedAtom = nearest; selectedIssue = null; render();
    }
    drag = null;
  });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  document.addEventListener('keydown', event => {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
    if (event.key === 'Escape') toggleIR(false);
    if (event.code === 'Space') { event.preventDefault(); $('play-button').click(); }
    if (event.key === 'ArrowRight') { event.preventDefault(); $('next-step').click(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); $('previous-step').click(); }
  });
  new ResizeObserver(resize).observe(canvas.parentElement);
  loadTrace(0);
})();
