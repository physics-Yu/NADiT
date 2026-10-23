(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const data = JSON.parse($('atom-replay-data').textContent);
  const canvas = $('atom-canvas');
  const context = canvas.getContext('2d');
  const stage = $('stage');
  const strip = $('step-strip');
  const stripContext = strip.getContext('2d');

  // Original light palette; keep in sync with viewer.css tokens.
  const colors = {
    stage: '#fcfdfd', grid: '#eaf0f2', site: '#dce3e7',
    ink: '#243748', muted: '#74808d',
    slm: '#4772b1', aod: '#147b7b', cz: '#9b65a8', local: '#c58b35', readout: '#b96c25', warn: '#b96c25',
    risk: '#b25138', wait: '#b8c3cb',
  };
  const kindColor = kind => kind === 'CZPulse' ? colors.cz
    : kind === 'Measure' ? colors.readout
      : kind === 'Load' ? '#529e9e' : kind === 'Store' ? '#8ab8b1' : kind === 'Move' ? colors.aod
        : ['Rotation', 'LocalU', 'GlobalRY', 'Reset'].includes(kind) ? colors.local
          : colors.wait;
  const KIND_PRIORITY = ['CZPulse', 'Measure', 'Move', 'Load', 'Store', 'GlobalRY', 'Rotation', 'LocalU', 'Reset', 'Wait'];
  const KIND_LABELS = {Load: 'Load: SLM → AOD', Move: 'Move atoms', Store: 'Store: AOD → SLM',
    CZPulse: 'CZ pulse', Measure: 'Readout', Rotation: 'Local rotation', LocalU: 'Local gate',
    GlobalRY: 'Global Y rotation', Reset: 'Reset', Wait: 'Wait'};
  const numeric = (value, digits = 2) => (Math.abs(value) < 1e-9 ? 0 : Number(value)).toLocaleString('en-US', { maximumFractionDigits: digits });
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const alpha = (hex, a) => hex + Math.round(clamp(a, 0, 1) * 255).toString(16).padStart(2, '0');
  const camera = { x: 0, y: 0, scale: 1, width: 1, height: 1 };
  let trace, replay, frame, cursor = { step: 0, fraction: 0 };
  let playing = false, previousTimestamp = null, animationId = null, selectedAtom = null;
  let currentStep = -1, currentMeasurementCount = -1, selectedIssue = null;
  let actionCards = [], drag = null, presenting = false, idleTimer = null;
  let analysis, risk;
  let hoveredStep = null, focusBeforePresent = null;
  const dashboard = new AtomReplayDashboard.Dashboard(
    time => { const target = replay.cursorAtTime(time); seek(target.step, target.fraction); },
    atom => { selectedAtom = atom; render(); }
  );
  const pct = value => numeric(value * 100, 3) + '%';
  const ui = () => presenting ? 1.7 : 1;  // presentation scale for marks and text
  const FONT = 'Bahnschrift, "DIN Alternate", Barlow, "Segoe UI", sans-serif';

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
    // In presentation the HUD (top) and control overlay (bottom) cover the field;
    // fit the layout into the band between them.
    const top = Math.min(presenting ? 110 : 60, camera.height * .2);
    const bottom = Math.min(presenting ? 290 : 50, camera.height * .45);
    const padX = Math.min(presenting ? 260 : 200, camera.width * .4);
    camera.x = (bounds[0] + bounds[2]) / 2;
    camera.scale = Math.max(.001, Math.min((camera.width - padX) / Math.max(10, bounds[2] - bounds[0]), (camera.height - top - bottom) / Math.max(10, bounds[3] - bounds[1])));
    camera.y = (bounds[1] + bounds[3]) / 2 - (bottom - top) / (2 * camera.scale);
    render();
  }
  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    camera.width = rect.width; camera.height = rect.height;
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawStrip();
    render();
  }

  // Stage layers --------------------------------------------------------
  function drawField() {
    context.fillStyle = colors.stage;
    context.fillRect(0, 0, camera.width, camera.height);
  }
  function drawAxes() {
    const target = 90 / camera.scale;
    const exponent = Math.pow(10, Math.floor(Math.log10(target)));
    const interval = [1, 2, 5, 10].find(value => value * exponent >= target) * exponent;
    const bottomLeft = toWorld(0, camera.height), topRight = toWorld(camera.width, 0);
    context.strokeStyle = colors.grid; context.lineWidth = 1;
    context.fillStyle = alpha(colors.muted, .8); context.font = 11 * ui() + 'px ' + FONT;
    for (let x = Math.ceil(bottomLeft[0] / interval) * interval; x <= topRight[0]; x += interval) {
      line(x, bottomLeft[1], x, topRight[1]);
      const sx = toScreen(x, 0)[0];
      if (!presenting && sx < camera.width - 160) context.fillText(numeric(x, 2), sx + 4, camera.height - 30);
    }
    for (let y = Math.ceil(bottomLeft[1] / interval) * interval; y <= topRight[1]; y += interval) {
      line(bottomLeft[0], y, topRight[0], y);
      if (!presenting) context.fillText(numeric(y, 2), 6, toScreen(0, y)[1] - 5);
    }
  }
  function zoneColor(zone) {
    const operations = zone.operations.join(' ').toLowerCase();
    return operations.includes('cz') ? colors.cz : operations.includes('measure') ? colors.readout : colors.ink;
  }
  function drawZones() {
    for (const zone of trace.device.zones) {
      const tint = zoneColor(zone);
      context.lineWidth = 1; context.setLineDash([6, 5]);
      rectangle(zone.bounds, alpha(tint, tint === colors.ink ? .008 : .045), alpha(tint, tint === colors.ink ? .22 : .4));
      context.setLineDash([]);
      // Label sits left of the zone, vertically centred, so it never covers atoms.
      const left = toScreen(zone.bounds[0], (zone.bounds[1] + zone.bounds[3]) / 2);
      context.fillStyle = alpha(tint, .9); context.font = 600 + ' ' + 12 * ui() + 'px ' + FONT;
      context.textAlign = 'right'; context.textBaseline = 'middle';
      context.fillText(zone.id, left[0] - 12 * ui(), left[1]);
      context.textAlign = 'start'; context.textBaseline = 'alphabetic';
    }
    if ($('sites-toggle').checked) {
      context.lineWidth = 1;
      const radius = clamp(camera.scale * .45, 1.2, 4) * ui();
      for (const site of trace.device.slm_sites) circle(site.x, site.y, radius, null, colors.site);
    }
  }
  function drawGrids() {
    if (!$('grid-toggle').checked) return;
    for (const grid of frame.grids.values()) {
      const device = trace.device.aods.find(aod => aod.id === grid.aod_id);
      const bounds = device ? device.workspace : worldBounds();
      context.strokeStyle = alpha(colors.aod, .3); context.lineWidth = 1.2 * ui();
      for (const row of grid.rows) line(bounds[0], row.coordinate, bounds[2], row.coordinate);
      for (const column of grid.columns) line(column.coordinate, bounds[1], column.coordinate, bounds[3]);
      for (const row of grid.rows) {
        for (const column of grid.columns) circle(column.coordinate, row.coordinate, 2 * ui(), alpha(colors.aod, .45));
      }
      if ($('labels-toggle').checked) {
        context.font = 11 * ui() + 'px ' + FONT; context.fillStyle = alpha(colors.aod, .8);
        for (const row of grid.rows) { const p = toScreen(bounds[0], row.coordinate); context.fillText('r' + row.id, p[0] + 3, p[1] - 3); }
        for (const column of grid.columns) { const p = toScreen(column.coordinate, bounds[3]); context.fillText('c' + column.id, p[0] + 3, p[1] - 4); }
      }
    }
  }
  function drawMoves() {
    if (!$('trails-toggle').checked) return;
    for (const action of frame.actions) {
      if (action.kind !== 'Move') continue;
      context.strokeStyle = alpha(colors.aod, .55); context.lineWidth = 1.4 * ui(); context.setLineDash([5 * ui(), 5 * ui()]);
      for (const points of frame.movePaths.get(action).atoms.values()) {
        const destination = points[points.length - 1];
        for (let i = 1; i < points.length; i++) line(points[i - 1].x, points[i - 1].y, points[i].x, points[i].y);
        circle(destination.x, destination.y, 5 * ui(), null, alpha(colors.aod, .55));
      }
      context.setLineDash([]);
    }
  }
  function drawPulses() {
    for (const action of frame.actions) {
      if (!action.coverage || actionProgress(action) === 1) continue;
      const color = action.kind === 'CZPulse' ? colors.cz : colors.local;
      context.lineWidth = 1.5 * ui();
      for (const bounds of action.coverage.regions) rectangle(bounds, alpha(color, .08), alpha(color, .55));
      context.save();
      context.shadowColor = alpha(color, .5); context.shadowBlur = 6 * ui();
      for (const pair of action.pairs) {
        const a = frame.atoms.get(pair[0]), b = frame.atoms.get(pair[1]);
        if (!a || !b) continue;
        context.strokeStyle = color; context.lineWidth = 3 * ui(); line(a.x, a.y, b.x, b.y);
      }
      context.restore();
    }
  }
  function actionHighlights() {
    const highlights = new Map();
    for (const action of frame.actions) {
      if (actionProgress(action) === 1) continue;
      if (!['CZPulse', 'Measure', 'Rotation', 'LocalU', 'GlobalRY', 'Reset'].includes(action.kind)) continue;
      for (const atomId of action.atoms) highlights.set(atomId, kindColor(action.kind));
    }
    return highlights;
  }
  function drawAtoms() {
    const highlights = actionHighlights();
    const radius = clamp(camera.scale * .7, 3.2, 7) * ui();
    for (const atom of frame.atoms.values()) {
      const point = toScreen(atom.x, atom.y);
      if (point[0] < -40 || point[0] > camera.width + 40 || point[1] < -40 || point[1] > camera.height + 40) continue;
      const lossColour = $('risk-toggle').checked && risk;
      const core = lossColour ? `hsl(${175 - 155 * (risk.atoms.get(atom.id) / analysis.maxAtomLoss)} 48% 47%)`
        : atom.holder.kind === 'aod' ? colors.aod : colors.slm;
      if (highlights.has(atom.id)) {
        const tint = highlights.get(atom.id);
        circle(atom.x, atom.y, radius * 2.4, alpha(tint, .16));
        context.lineWidth = 2 * ui(); circle(atom.x, atom.y, radius * 1.9, null, tint);
      }
      context.lineWidth = 1.4 * ui(); circle(atom.x, atom.y, radius, core, '#ffffff');
      if (risk?.maximum.atom === atom.id) {
        context.lineWidth = 2 * ui(); circle(atom.x, atom.y, radius + 8 * ui(), null, colors.risk);
        context.fillStyle = colors.risk; context.font = 600 + ' ' + 11 * ui() + 'px ' + FONT;
        context.fillText('Highest loss, atom ' + atom.id, point[0] + radius + 6 * ui(), point[1] - radius - 10 * ui());
      }
      if (atom.id === selectedAtom) { context.lineWidth = 2 * ui(); circle(atom.x, atom.y, radius + 6 * ui(), null, colors.ink); }
      if ($('labels-toggle').checked || atom.id === selectedAtom) {
        context.fillStyle = colors.ink; context.font = 12 * ui() + 'px ' + FONT;
        context.fillText(String(atom.id), point[0] + radius + 5, point[1] - radius - 3);
      }
    }
    if (selectedIssue) {
      context.lineWidth = 2 * ui();
      circle(selectedIssue.x, selectedIssue.y, radius + 14 * ui(), null, colors.warn);
    }
  }
  function drawScale() {
    const target = 110 / camera.scale;
    const exponent = Math.pow(10, Math.floor(Math.log10(target)));
    const distance = ([5, 2, 1].find(value => value * exponent <= target) || 1) * exponent;
    const width = distance * camera.scale;
    // In presentation the bottom band belongs to the controls; keep the bar under the HUD.
    const y = presenting ? 96 : camera.height - 40, right = camera.width - (presenting ? 32 : 20);
    context.strokeStyle = colors.ink; context.lineWidth = 2 * ui();
    context.beginPath(); context.moveTo(right - width, y); context.lineTo(right, y); context.stroke();
    context.font = 12 * ui() + 'px ' + FONT; context.fillStyle = colors.ink;
    context.fillText(numeric(distance) + ' µm', right - width, y - 8);
  }
  function draw() {
    drawField(); drawAxes(); drawZones(); drawMoves(); drawGrids(); drawPulses(); drawAtoms(); drawScale();
    const fallback = [...frame.movePaths.values()].some(path => path.reason);
    $('path-note').textContent = replay.motion === 'direct' ? 'Direct preview, recorded timing' : fallback ? 'No display lane, direct preview' : 'Lane preview, recorded timing';
  }

  // Step strip: one segment per step, coloured by its dominant action ------
  function dominantKind(step) {
    const kinds = new Set(step.actions.map(action => action.kind));
    return KIND_PRIORITY.find(kind => kinds.has(kind)) || 'Wait';
  }
  function describeStep(index) {
    const step = trace.steps[index];
    if (!step) return trace.duration_us > trace.program_duration_us
      ? 'Waiting for measurement results · ' + numeric(trace.program_duration_us) + '–' + numeric(trace.duration_us) + ' µs'
      : 'Final layout · ' + numeric(trace.duration_us) + ' µs';
    const kinds = [...new Set(step.actions.map(action => action.kind))];
    const atoms = new Set(step.actions.flatMap(action => action.atoms));
    return 'Step ' + (index + 1) + ' · ' + kinds.map(kind => KIND_LABELS[kind] || kind).join(' + ')
      + (atoms.size ? ' · ' + atoms.size + ' atoms' : '')
      + ' · ' + numeric(step.start_us) + '–' + numeric(step.start_us + step.duration_us) + ' µs';
  }
  function updateStepPreview() {
    const summary = describeStep(hoveredStep === null ? cursor.step : hoveredStep);
    $('step-preview').textContent = summary;
    $('step-slider').title = summary;
  }
  function drawStrip() {
    if (!trace) return;
    const rect = strip.getBoundingClientRect(), ratio = window.devicePixelRatio || 1;
    strip.width = Math.max(1, Math.round(rect.width * ratio)); strip.height = Math.max(1, Math.round(rect.height * ratio));
    stripContext.setTransform(ratio, 0, 0, ratio, 0, 0);
    stripContext.clearRect(0, 0, rect.width, rect.height);
    const count = trace.steps.length;
    // Segments are centred on the range input's integer values, including its end state.
    for (let index = 0; index <= count; index++) {
      const left = count ? clamp((index - .5) / count, 0, 1) * rect.width : 0;
      const right = count ? clamp((index + .5) / count, 0, 1) * rect.width : rect.width;
      stripContext.fillStyle = index < count ? kindColor(dominantKind(trace.steps[index])) : colors.wait;
      stripContext.globalAlpha = index <= cursor.step ? 1 : .45;
      stripContext.fillRect(left, 0, Math.max(1, right - left - (right - left > 4 ? 1 : 0)), rect.height);
    }
    stripContext.globalAlpha = 1;
  }

  // Panels --------------------------------------------------------------
  function buildActionList() {
    const container = $('action-list'); container.replaceChildren(); actionCards = [];
    $('action-count').textContent = frame.actions.length === 1 ? '1 action' : frame.actions.length + ' actions';
    if (!frame.actions.length) {
      container.append(element('p', 'empty-state', frame.time_us < trace.duration_us ? 'Waiting for a measurement result to become available.' : 'Program complete. The final atom layout is shown.'));
      return;
    }
    for (const action of frame.actions) {
      const card = element('article', 'action-card');
      card.dataset.kind = action.kind;
      const heading = element('div', 'action-title');
      heading.append(element('strong', null, action.kind), element('span', null, numeric(action.duration_us) + ' µs'));
      card.append(heading, element('pre', null, action.text));
      const details = ['Action ' + action.index, action.source_ids.length ? 'source ' + action.source_ids.join(', ') : 'no source ID'];
      if (action.pairs.length) details.push(action.pairs.length + (action.pairs.length === 1 ? ' CZ pair' : ' CZ pairs'));
      card.append(element('div', 'action-meta', details.join(', ')));
      const progress = element('div', 'action-progress'), bar = element('i'); progress.append(bar); card.append(progress);
      container.append(card); actionCards.push({ card, bar, action });
    }
  }
  function updateInspector() {
    const atom = frame.atoms.get(selectedAtom);
    const inspector = $('atom-inspector'); inspector.replaceChildren();
    $('selection-chip').hidden = !atom;
    $('selected-atom-label').textContent = atom ? 'Atom ' + atom.id : 'No selection';
    if (!atom) { inspector.className = 'empty-state'; inspector.textContent = 'Click an atom on the stage to see its qubit, position and trap.'; return; }
    $('selection-chip').textContent = 'Atom ' + atom.id + (atom.holder.kind === 'aod' ? ' in AOD' : ' in SLM');
    inspector.className = 'detail-grid';
    const holder = atom.holder;
    const details = [['Atom', atom.id], ['Physical qubit', atom.qubit === null ? 'none' : atom.qubit], ['x (µm)', numeric(atom.x, 4)], ['y (µm)', numeric(atom.y, 4)], ['Trap', holder.kind === 'slm' ? 'SLM site ' + holder.site_id : 'AOD ' + holder.aod_id], ...(holder.kind === 'aod' ? [['Row, column', holder.row + ', ' + holder.column]] : [])];
    for (const [label, value] of details) { const cell = element('div'); cell.append(element('small', null, label), element('strong', null, String(value))); inspector.append(cell); }
  }
  function updateMeasurements() {
    if (currentMeasurementCount === frame.measurements.length) return;
    currentMeasurementCount = frame.measurements.length;
    $('measurement-count').textContent = frame.measurements.length + ' available';
    const list = $('measurement-list'); list.replaceChildren();
    if (!frame.measurements.length) list.append(element('p', 'empty-state', 'No result is available yet at this time.'));
    for (const record of frame.measurements) {
      const row = element('div', 'measurement-row');
      row.append(element('span', null, 'm' + record.measurement_id), element('strong', null, String(record.value)), element('small', null, 'at ' + numeric(record.available_at_us) + ' µs'));
      list.append(row);
    }
  }
  function stepLabel() {
    return cursor.step < trace.steps.length ? [...new Set(frame.actions.map(action => KIND_LABELS[action.kind] || action.kind))].join(' + ') : frame.time_us < trace.duration_us ? 'Waiting for measurement results' : 'Final layout';
  }
  function updateControls() {
    $('step-slider').value = cursor.step;
    $('fraction-slider').value = Math.round(cursor.fraction * 1000);
    $('fraction-value').textContent = numeric(cursor.fraction * 100, 1) + '%';
    if (document.activeElement !== $('time-input')) $('time-input').value = Number(frame.time_us.toFixed(6));
    const position = cursor.step < trace.steps.length ? (cursor.step + 1) + ' / ' + trace.steps.length : 'End / ' + trace.steps.length;
    $('step-position').textContent = position;
    $('step-kind').textContent = stepLabel();
    $('step-slider').setAttribute('aria-valuetext', describeStep(cursor.step));
    updateStepPreview();
    $('hud-step').textContent = 'Step ' + position;
    $('hud-kind').textContent = stepLabel();
    $('hud-time').textContent = numeric(frame.time_us, 1);
    $('previous-step').disabled = cursor.step === 0 && cursor.fraction === 0;
    $('next-step').disabled = cursor.step === trace.steps.length && cursor.fraction === 1;
    for (const { card, bar, action } of actionCards) {
      const fraction = actionProgress(action);
      card.classList.toggle('active', fraction < 1); card.classList.toggle('complete', fraction === 1);
      bar.style.width = fraction * 100 + '%';
    }
  }
  function updateLossHud() {
    $('hud-loss').hidden = !risk;
    $('legend-risk').hidden = !(risk && $('risk-toggle').checked);
    if (risk) $('hud-loss').textContent = 'Mean loss ' + pct(risk.average) + ' · Max ' + pct(risk.maximum.probability);
  }
  function actionProgress(action) {
    return AtomReplayModel.actionProgress(action.duration_us, trace.steps[cursor.step].duration_us, frame.elapsed_us, frame.fraction);
  }
  let stripStep = -1;
  function render() {
    if (!replay) return;
    frame = replay.frame(cursor.step, cursor.fraction);
    if (currentStep !== cursor.step) { currentStep = cursor.step; buildActionList(); }
    if (stripStep !== cursor.step) { stripStep = cursor.step; drawStrip(); }
    risk = dashboard.update(frame.time_us, selectedAtom, cursor.step);
    updateLossHud();
    updateControls(); updateInspector(); updateMeasurements(); draw();
  }

  // Playback ------------------------------------------------------------
  function setPlaying(value) {
    if (animationId !== null) cancelAnimationFrame(animationId);
    animationId = null;
    playing = value; previousTimestamp = null;
    markActive();
    $('play-button').textContent = playing ? 'Pause' : 'Play';
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
      button.type = 'button';
      button.append(element('strong', null, 'Atom ' + issue.atom_id + ', ' + (issue.step_index < 0 ? 'initial layout' : 'after step ' + (issue.step_index + 1))), element('span', null, numeric(issue.time_us) + ' µs: ' + issue.message));
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
    cursor = { step: 0, fraction: 0 }; selectedAtom = null; selectedIssue = null; currentStep = -1; currentMeasurementCount = -1; stripStep = -1; hoveredStep = null;
    $('trace-title').textContent = trace.title; $('trace-description').textContent = trace.description;
    $('atom-count').textContent = trace.initial.atoms.length; $('step-count').textContent = trace.steps.length;
    $('duration-total').textContent = numeric(trace.duration_us); $('device-name').textContent = 'Device: ' + trace.device.name;
    $('step-slider').max = trace.steps.length; $('time-input').max = trace.duration_us;
    $('ir-source').textContent = trace.atom_ir; buildIssues(); resize(); fitView();
  }

  // Presentation mode ---------------------------------------------------
  function markActive() {
    stage.classList.remove('is-idle');
    clearTimeout(idleTimer);
    if (presenting && playing) idleTimer = setTimeout(() => stage.classList.add('is-idle'), 2500);
  }
  function applyPresenting(value) {
    presenting = value;
    stage.classList.toggle('is-presenting', value);
    document.body.classList.toggle('presenting', value);
    $('exit-present').hidden = !value;
    $('present-button').setAttribute('aria-pressed', String(value));
    markActive();
    requestAnimationFrame(() => { resize(); fitView(); (value ? canvas : focusBeforePresent || $('present-button')).focus({ preventScroll: true }); });
  }
  async function enterPresent() {
    focusBeforePresent = document.activeElement;
    applyPresenting(true);
    if (stage.requestFullscreen) {
      try {
        await stage.requestFullscreen();
        if (!presenting && document.fullscreenElement === stage) await document.exitFullscreen();
      } catch { /* Browser disallows native fullscreen; retain the in-window presentation. */ }
    }
  }
  async function exitPresent() {
    if (document.fullscreenElement === stage) {
      try { await document.exitFullscreen(); }
      catch { markActive(); return; } // Native Esc remains available if exit is denied.
    }
    applyPresenting(false);
  }
  document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && presenting) applyPresenting(false); });
  $('present-button').addEventListener('click', enterPresent);
  $('exit-present').addEventListener('click', exitPresent);
  stage.addEventListener('pointermove', markActive);
  stage.addEventListener('focusin', markActive);

  // Events --------------------------------------------------------------
  data.traces.forEach((record, index) => { const option = element('option', null, record.title); option.value = index; $('trace-select').append(option); });
  $('trace-select').addEventListener('change', event => loadTrace(Number(event.target.value)));
  $('path-select').addEventListener('change', event => {
    replay = new AtomReplayModel.Replay(trace, { motion: event.target.value });
    currentStep = -1; render();
  });
  $('play-button').addEventListener('click', () => {
    if (!playing && cursor.step === trace.steps.length && (cursor.fraction === 1 || trace.duration_us === trace.program_duration_us)) seek(0, 0);
    setPlaying(!playing);
  });
  $('previous-step').addEventListener('click', () => seek(cursor.fraction > 0 ? cursor.step : cursor.step - 1));
  $('next-step').addEventListener('click', () => seek(cursor.step < trace.steps.length ? cursor.step + 1 : cursor.step, cursor.step >= trace.steps.length ? 1 : 0));
  $('step-slider').addEventListener('input', event => { hoveredStep = null; seek(Number(event.target.value)); });
  $('step-slider').addEventListener('pointermove', event => {
    const rect = $('step-slider').getBoundingClientRect();
    hoveredStep = Math.round(clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1) * trace.steps.length);
    updateStepPreview();
  });
  $('step-slider').addEventListener('pointerleave', () => { hoveredStep = null; updateStepPreview(); });
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
  function toggleIR(open) { $('ir-panel').hidden = !open; $('ir-toggle').setAttribute('aria-expanded', String(open)); $('ir-toggle').textContent = open ? 'Hide Atom IR' : 'Show Atom IR'; }
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
    $('view-position').textContent = 'x ' + numeric(world[0]) + ', y ' + numeric(world[1]) + ' µm';
    if (!drag) return;
    camera.x -= (event.clientX - drag.lastX) / camera.scale; camera.y += (event.clientY - drag.lastY) / camera.scale;
    drag.lastX = event.clientX; drag.lastY = event.clientY; render();
  });
  canvas.addEventListener('pointerup', event => {
    if (!drag) return;
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) {
      const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
      let nearest = null, distance = 16 * ui();
      for (const atom of frame.atoms.values()) { const p = toScreen(atom.x, atom.y), d = Math.hypot(x - p[0], y - p[1]); if (d < distance) { nearest = atom.id; distance = d; } }
      selectedAtom = nearest; selectedIssue = null; render();
    }
    drag = null;
  });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      toggleIR(false);
      if (presenting) { event.preventDefault(); exitPresent(); }
      return;
    }
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) return;
    markActive();
    if (event.key === 'f' || event.key === 'F') { event.preventDefault(); presenting ? exitPresent() : enterPresent(); }
    if ((event.key === 'l' || event.key === 'L') && analysis?.hasNoise) { $('risk-toggle').checked = !$('risk-toggle').checked; render(); }
    if (event.code === 'Space') { event.preventDefault(); $('play-button').click(); }
    if (event.key === 'ArrowRight') { event.preventDefault(); $('next-step').click(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); $('previous-step').click(); }
  });
  new ResizeObserver(resize).observe(canvas.parentElement);
  new ResizeObserver(drawStrip).observe(strip.parentElement);
  loadTrace(0);
})();
