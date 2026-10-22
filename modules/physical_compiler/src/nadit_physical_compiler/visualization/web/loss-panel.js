/* Source comparison rendering. Both probability columns use one absolute scale. */
(function(root) {
  'use strict';
  const $=id=>document.getElementById(id);
  const fmt=x=>Number(x).toLocaleString('en-US',{maximumFractionDigits:6});
  const pct=x=>fmt(x*100)+'%';
  const sources=['load','store','move','residence','cz','other'];
  class Panel {
    constructor(select) {
      this.maximumAtom=null;
      $('maximum-loss-atom').addEventListener('click',()=>{if(this.maximumAtom!==null)select(this.maximumAtom);});
    }
    update(risk) {
      $('loss-panel').hidden=!risk;
      this.maximumAtom=risk?.maximum.atom ?? null;
      $('maximum-loss-atom').disabled=this.maximumAtom===null;
      $('average-loss-now').textContent=risk?pct(risk.average):'—';
      $('maximum-loss-now').textContent=risk?pct(risk.maximum.probability):'—';
      const atomLabel=this.maximumAtom===null?'No accumulated loss':`Atom ${this.maximumAtom}${risk.maximum.ties>1 ? ' · '+risk.maximum.ties+' tied' : ''}`;
      $('maximum-loss-atom').textContent=atomLabel;
      $('loss-maximum-label').textContent=this.maximumAtom===null?'Highest-risk atom':`Atom ${this.maximumAtom} · loss`;
      if(!risk)return;
      const maximum=risk.atomContributions.get(this.maximumAtom) || {};
      const scale=risk.maximum.probability || 1;
      for(const source of sources) {
        const mean=risk.averageContributions[source],max=maximum[source] || 0;
        $('loss-'+source+'-row').hidden=['cz','other'].includes(source) && risk.contributions[source]===0;
        $('loss-'+source+'-expected').textContent=fmt(risk.contributions[source]);
        $('loss-'+source+'-average').textContent=pct(mean);
        $('loss-'+source+'-maximum').textContent=pct(max);
        $('loss-'+source+'-average-bar').style.width=(100*mean/scale)+'%';
        $('loss-'+source+'-maximum-bar').style.width=(100*max/scale)+'%';
      }
      $('loss-total-expected').textContent=fmt(risk.expected);
      $('loss-total-average').textContent=pct(risk.average);
      $('loss-total-maximum').textContent=pct(risk.maximum.probability);
      $('loss-comparison-scale').textContent='Shared probability scale: 0–'+pct(risk.maximum.probability);
    }
  }
  root.AtomReplayLossPanel={Panel};
})(window);
