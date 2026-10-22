/* Completion-booked first-loss attribution, independent of replay and DOM. */
(function(root) {
  'use strict';
  const SOURCES = ['load','store','move','residence','cz','other'];
  const empty = () => Object.fromEntries(SOURCES.map(source => [source,0]));
  const sourceOf = event => ({load:'load',store:'store',move:'move',holder_lifetime:'residence',configured_cz_atom_loss:'cz'})[event.cause || event.kind?.toLowerCase()] || 'other';
  function upperBound(xs,time) {
    let low=0,high=xs.length;
    while(low<high) {const mid=(low+high)>>>1;if(xs[mid].end<=time)low=mid+1;else high=mid;}
    return low;
  }
  function indexEvents(events) {
    events.sort((a,b)=>a.end-b.end);
    const snapshots=[]; let total=0,contributions=empty();
    for(let start=0;start<events.length;) {
      let end=start+1;
      while(end<events.length && events[end].end===events[start].end)end++;
      const hazards=empty(), certain=empty();
      for(let i=start;i<end;i++) {
        const event=events[i];
        if(event.hazard===Infinity)certain[event.source]++;
        else hazards[event.source]+=event.hazard;
      }
      const count=Object.values(certain).reduce((a,b)=>a+b,0);
      const hazard=count ? Infinity : Object.values(hazards).reduce((a,b)=>a+b,0);
      const increment=Math.exp(-total)*-Math.expm1(-hazard);
      contributions={...contributions};
      if(hazard>0)for(const source of SOURCES)contributions[source]+=increment*(count ? certain[source]/count : hazards[source]/hazard);
      total+=hazard;
      snapshots.push({end:events[start].end,total,contributions});
      start=end;
    }
    return snapshots;
  }
  class LossIndex {
    constructor(atomIds,events) {
      const grouped=new Map(atomIds.map(id=>[id,[]]));
      for(const event of events)grouped.get(event.atom).push({end:event.start_us+event.duration_us,hazard:-Math.log(event.survival_probability),source:sourceOf(event)});
      this.atoms=new Map([...grouped].map(([id,rows])=>[id,indexEvents(rows)]));
      this.times=[...new Set([...this.atoms.values()].flat().map(e=>e.end))].sort((a,b)=>a-b);
    }
    at(time) {
      const atoms=new Map(),atomContributions=new Map(),contributions=empty();
      let expected=0,total=0;
      const maximum={atom:null,probability:0,ties:0};
      for(const [id,events] of this.atoms) {
        const index=upperBound(events,time)-1, snapshot=index<0?null:events[index];
        const hazard=snapshot?.total || 0, loss=-Math.expm1(-hazard), sources=snapshot?.contributions || empty();
        atoms.set(id,loss);atomContributions.set(id,{...sources});expected+=loss;total+=hazard;
        for(const source of SOURCES)contributions[source]+=sources[source];
        if(loss>maximum.probability)Object.assign(maximum,{atom:id,probability:loss,ties:1});
        else if(loss>0 && loss===maximum.probability) {maximum.atom=Math.min(maximum.atom,id);maximum.ties++;}
      }
      const count=this.atoms.size;
      const averageContributions=Object.fromEntries(SOURCES.map(source=>[source,count?contributions[source]/count:0]));
      return {atoms,expected,allSurvive:Math.exp(-total),average:count?expected/count:0,maximum,contributions,averageContributions,atomContributions};
    }
  }
  const api={LossIndex,SOURCES};root.AtomReplayLoss=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
