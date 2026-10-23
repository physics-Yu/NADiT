/* Linked schedule, cumulative loss and current-step noise inspection. */
(function(root) {
  'use strict';
  const $ = id => document.getElementById(id);
  const fmt = (x, digits=3) => Number(x).toLocaleString('en-US', {maximumFractionDigits:digits});
  const pct = x => fmt(x * 100, 4) + '%';
  const palette = {Load:'#57a7a1', Move:'#188b86', Store:'#87b9b2', CZPulse:'#aa72b7', Rotation:'#cba056', GlobalRY:'#cba056', LocalU:'#cba056', Measure:'#ca793d', Reset:'#8797ad', Wait:'#b8c3cb'};
  class Dashboard {
    constructor(seek, select) {
      this.seek = seek; this.select = select; this.time = 0;
      this.lossPanel = new root.AtomReplayLossPanel.Panel(select);
      this.canvas = $('schedule-canvas'); this.ctx = this.canvas.getContext('2d');
      for (const id of ['schedule-mode', 'schedule-span']) $(id).addEventListener('change', () => this.drawSchedule());
      $('risk-atom').addEventListener('change', e => this.select(e.target.value === '' ? null : Number(e.target.value)));
      this.canvas.addEventListener('click', e => {
        const rect = this.canvas.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
        if (x < 154) return;
        const t = Math.max(this.range[0], Math.min(this.range[1], this.range[0] + (x-154)/(rect.width-166)*(this.range[1]-this.range[0])));
        const event = this.analysis.hit(Math.floor((y-27)/22), t);
        if (event) {
          $('schedule-detail').textContent = `${event.action.kind} · ${fmt(event.start)}–${fmt(event.end)} µs · action ${event.action.index} · ${event.action.atoms.length} atoms · source ${event.action.source_ids.join(', ') || 'transport'}`;
          this.seek(event.start);
          if (event.action.atoms.length) this.select(event.action.atoms[0]);
        } else { this.seek(t); $('schedule-detail').textContent = `Time ${fmt(t)} µs`; }
      });
      new ResizeObserver(() => this.drawSchedule()).observe(this.canvas.parentElement);
    }
    load(trace, analysis) {
      this.trace = trace; this.analysis = analysis; this.lastStep = -2;
      const report = trace.noise_report;
      $('risk-status').textContent = report ? report.model + '/' + report.version : 'No noise report';
      $('risk-content').hidden = !report;
      $('risk-empty').hidden = !!report;
      $('risk-atom').replaceChildren(new Option('Select an atom', ''));
      for (const atom of trace.initial.atoms) $('risk-atom').add(new Option(`Atom ${atom.id} · q${atom.qubit ?? '—'}`, atom.id));
      $('risk-final').textContent = report ? fmt(analysis.finalRisk.expected, 6) : '—';
      $('fidelity-final').textContent = report ? pct(report.metrics.fidelity) : '—';
      $('risk-scale').textContent = report ? `Loss color · 0–${pct(analysis.maxAtomLoss)}` : 'Loss color unavailable';
      $('noise-params').textContent = report ? JSON.stringify(report.params, null, 2) : '';
      $('schedule-detail').textContent = 'Click an action to seek and inspect its atoms.';
    }
    update(time, selected, step) {
      this.time = time;
      const risk = this.analysis.riskAt(time);
      $('risk-now').textContent = risk ? fmt(risk.expected, 6) : '—';
      this.lossPanel.update(risk);
      $('risk-atom').value = selected === null ? '' : selected;
      $('atom-risk').textContent = risk && selected !== null ? pct(risk.atoms.get(selected)) : 'Select an atom';
      if (this.lastStep !== step) {
        this.lastStep = step;
        const noise = this.trace.noise_report?.noise;
        const gates = (noise?.gate_diagnostics || []).filter(g => g.step_index === step);
        const channels = (noise?.channels || []).filter(c => c.step_index === step);
        const leakage = gates.filter(g => 'average_leakage_probability' in g || 'average_terminal_leakage_probability' in g);
        $('leakage-summary').textContent = leakage.length ? leakage.slice(0,4).map(g =>
          `Atoms ${g.atoms.join('/')} · leakage ${pct(g.average_terminal_leakage_probability ?? g.average_leakage_probability)} · unresolved loss ${pct(g.average_unresolved_loss_probability || 0)}`
        ).join('\n') : 'No upstream leakage estimate for this step.';
        $('noise-count').textContent = channels.length + ' channels';
        const ids = [...new Set(gates.map(g => g.upstream_record_id).filter(Boolean))];
        $('noise-step').textContent = JSON.stringify({channels, gate_diagnostics:gates,
          upstream_records:Object.fromEntries(ids.map(id => [id, noise.upstream_records[id]]))}, null, 2);
      }
      this.drawSchedule();
      return risk;
    }
    drawSchedule() {
      if (!this.analysis) return;
      const ctx = this.ctx, width = this.canvas.parentElement.clientWidth, top = 27, row = 22;
      const riskTop = top + row*this.analysis.lanes.length + 12, height = riskTop + 64;
      const dpr = window.devicePixelRatio || 1;
      if (this.canvas.width !== Math.round(width*dpr) || this.canvas.height !== Math.round(height*dpr)) {
        this.canvas.width = Math.round(width*dpr); this.canvas.height = Math.round(height*dpr);
        this.canvas.style.height = height + 'px';
      }
      ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
      const total = this.trace.duration_us || 1, span = Math.min(total, Math.max(1, Number($('schedule-span').value) || 1000));
      const start = $('schedule-mode').value === 'all' ? 0 : Math.max(0,Math.min(total-span,this.time-span*.25));
      const end = $('schedule-mode').value === 'all' ? total : start+span;
      this.range = [start,end];
      const x = t => 154+(t-start)/(end-start)*(width-166);
      ctx.font='10px Segoe UI';
      for(let i=0;i<=4;i++) { const t=start+(end-start)*i/4, px=x(t); ctx.fillStyle='#70818d'; ctx.textAlign=i===4?'right':'left';ctx.fillText(fmt(t,0),px,13);ctx.strokeStyle='#edf1f4';ctx.beginPath();ctx.moveTo(px,20);ctx.lineTo(px,height-14);ctx.stroke(); }
      ctx.textAlign='left';
      this.analysis.lanes.forEach((lane,i) => {
        const y=top+i*row; ctx.fillStyle='#536876';
        ctx.fillText(lane.label.length>24 ? lane.label.slice(0,23)+'…' : lane.label, 0,y+11);
        ctx.fillStyle='#f3f6f8';ctx.fillRect(154,y,width-166,15);
        for(const event of lane.events) {
          if(event.end<start || event.start>end)continue;
          const a=Math.max(154,x(event.start)),b=Math.min(width-12,x(event.end));
          ctx.fillStyle=palette[lane.kind] || '#8b9aa5';ctx.fillRect(a,y,Math.max(1,b-a),15);
        }
      });
      ctx.fillStyle='#b46c36';ctx.fillText('Expected lost atoms',0,riskTop+12);
      if(this.analysis.hasNoise) {
        const maximum=Math.max(this.analysis.finalRisk.expected,1e-9);
        ctx.fillText('0 → '+fmt(this.analysis.finalRisk.expected,4),0,riskTop+29);
        ctx.strokeStyle='#c8844a';ctx.lineWidth=1.8;ctx.beginPath();
        let previous=riskTop+42-this.analysis.riskAt(start).expected/maximum*36;
        ctx.moveTo(x(start),previous);
        for(const [t,value] of this.analysis.curve) {
          if(t<=start || t>end)continue;
          const next=riskTop+42-value/maximum*36;
          ctx.lineTo(x(t),previous);ctx.lineTo(x(t),next);previous=next;
        }
        ctx.lineTo(x(end),previous);
        ctx.stroke();
      } else { ctx.fillStyle='#8897a1';ctx.fillText('Attach a noise/v1 report to show loss estimates.',154,riskTop+24); }
      ctx.strokeStyle='#283e4e';ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(x(this.time),19);ctx.lineTo(x(this.time),height-12);ctx.stroke();
      $('schedule-range').textContent=`${fmt(start,0)}–${fmt(end,0)} µs`;
    }
  }
  root.AtomReplayDashboard = {Dashboard};
})(window);
