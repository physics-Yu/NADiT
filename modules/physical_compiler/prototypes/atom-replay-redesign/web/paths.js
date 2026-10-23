/* Display-only paths. Atom IR endpoints and action durations remain authoritative. */
(function (root) {
  'use strict';
  const EPSILON = 1e-9;
  const MAX_OFFSET_UM = 2;
  const point = (record) => ({ x: record.x, y: record.y });
  function gapOffset(values) {
    const sorted = [...new Set(values)].sort((a, b) => a - b);
    let gap = Infinity;
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] - sorted[i - 1] > EPSILON) gap = Math.min(gap, sorted[i] - sorted[i - 1]);
    }
    return Math.min(MAX_OFFSET_UM, gap / 4);
  }
  function fitOffset(amount, values, lower, upper) {
    if (!values.length) return amount;
    const positive = upper - Math.max(...values), negative = Math.min(...values) - lower;
    if (positive > EPSILON) return Math.min(amount, positive / 2);
    if (negative > EPSILON) return -Math.min(amount, negative / 2);
    return 0;
  }
  function waypoints(source, target, offset) {
    if (!offset) return [point(source), point(target)];
    return [point(source),
      { x: source.x + offset.x, y: source.y + offset.y },
      { x: source.x + offset.x, y: target.y + offset.y },
      { x: target.x, y: target.y + offset.y }, point(target)];
  }
  function linePoints(source, target, axis, offset) {
    const initial = new Map(source.map(line => [line.id, line.coordinate]));
    return target.map(line => {
      const before = initial.get(line.id) ?? line.coordinate;
      const points = waypoints({ x: before, y: before }, { x: line.coordinate, y: line.coordinate }, offset);
      return { id: line.id, values: points.map(p => p[axis]) };
    });
  }
  function buildMovePath(action, entry, device = {}, mode = 'lanes') {
    const targets = new Map(action.patch.atoms.map(atom => [atom.id, atom]));
    // Sparse patches omit unchanged atoms, but display offsets move their lines too.
    for (const grid of action.patch.grids) {
      const rows = new Map(grid.rows.map(line => [line.id, line.coordinate]));
      const columns = new Map(grid.columns.map(line => [line.id, line.coordinate]));
      for (const atom of entry.atoms.values()) {
        if (atom.holder.kind === 'aod' && atom.holder.aod_id === grid.aod_id && !targets.has(atom.id)) {
          targets.set(atom.id, { ...atom, x: columns.get(atom.holder.column), y: rows.get(atom.holder.row) });
        }
      }
    }
    const targetAtoms = [...targets.values()];
    const starts = targetAtoms.map(atom => entry.atoms.get(atom.id));
    const sites = device.slm_sites || [];
    const allPoints = [...sites, ...entry.atoms.values(), ...targetAtoms];
    const xs = allPoints.map(p => p.x), ys = allPoints.map(p => p.y);
    for (const grid of [...entry.grids.values(), ...action.patch.grids]) {
      xs.push(...grid.columns.map(line => line.coordinate));
      ys.push(...grid.rows.map(line => line.coordinate));
    }
    const offsets = new Map();
    let reason = '';
    for (const target of action.patch.grids) {
      const source = entry.grids.get(target.aod_id) || { rows: [], columns: [] };
      const bounds = (device.aods || []).find(aod => aod.id === target.aod_id)?.workspace || [-Infinity, -Infinity, Infinity, Infinity];
      const x = fitOffset(gapOffset(xs), source.columns.map(line => line.coordinate), bounds[0], bounds[2]);
      const y = fitOffset(gapOffset(ys), [...source.rows, ...target.rows].map(line => line.coordinate), bounds[1], bounds[3]);
      if (Math.abs(x) <= EPSILON || Math.abs(y) <= EPSILON) reason = 'No common display offset inside AOD workspace';
      offsets.set(target.aod_id, { x, y });
    }
    const changed = targetAtoms.some((atom, i) => atom.x !== starts[i].x || atom.y !== starts[i].y)
      || action.patch.grids.some(grid => {
        const source = entry.grids.get(grid.aod_id);
        return ['rows', 'columns'].some(axis => grid[axis].some(line => source?.[axis].find(item => item.id === line.id)?.coordinate !== line.coordinate));
      });
    const useLanes = mode === 'lanes' && changed && !reason && offsets.size > 0;
    const atoms = new Map(targetAtoms.map((atom, i) => [atom.id,
      waypoints(starts[i], atom, useLanes ? offsets.get(starts[i].holder.aod_id) : null)]));
    const grids = new Map(action.patch.grids.map(target => {
      const source = entry.grids.get(target.aod_id) || { rows: [], columns: [] };
      const offset = useLanes ? offsets.get(target.aod_id) : null;
      return [target.aod_id, {
        aod_id: target.aod_id,
        rows: linePoints(source.rows, target.rows, 'y', offset),
        columns: linePoints(source.columns, target.columns, 'x', offset),
      }];
    }));
    const lengths = Array(useLanes ? 4 : 1).fill(0);
    for (const points of atoms.values()) {
      lengths.forEach((_, i) => { lengths[i] = Math.max(lengths[i], Math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)); });
    }
    // Empty active lines also participate in the shared animation clock.
    for (const grid of grids.values()) {
      for (const line of [...grid.rows, ...grid.columns]) {
        lengths.forEach((_, i) => { lengths[i] = Math.max(lengths[i], Math.abs(line.values[i + 1] - line.values[i])); });
      }
    }
    return { mode: useLanes ? 'lanes' : 'direct', reason: mode === 'lanes' && changed ? reason : '', atoms, grids, lengths };
  }
  function sampleMovePath(path, fraction) {
    let distance = Math.max(0, Math.min(1, fraction)) * path.lengths.reduce((a, b) => a + b, 0);
    let segment = 0;
    while (segment < path.lengths.length - 1 && distance > path.lengths[segment]) distance -= path.lengths[segment++];
    const part = path.lengths[segment] > 0 ? distance / path.lengths[segment] : 0;
    const interpolate = values => values[segment] + (values[segment + 1] - values[segment]) * part;
    return {
      atoms: new Map([...path.atoms].map(([id, points]) => [id, {
        x: interpolate(points.map(p => p.x)), y: interpolate(points.map(p => p.y)),
      }])),
      grids: new Map([...path.grids].map(([id, grid]) => [id, {
        aod_id: id,
        rows: grid.rows.map(line => ({ id: line.id, coordinate: interpolate(line.values) })),
        columns: grid.columns.map(line => ({ id: line.id, coordinate: interpolate(line.values) })),
      }])),
    };
  }
  const api = { buildMovePath, sampleMovePath };
  root.AtomReplayPaths = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
