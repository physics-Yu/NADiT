/* DOM/canvas adapter tests: no browser or visual acceptance is implied. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {Analysis} = require('../../src/nadit_physical_compiler/visualization/web/analysis.js');

test('dashboard displays bound losses and links schedule clicks to playback', () => {
  const elements = new Map();
  const context = new Proxy({}, {get:(obj,key) => obj[key] || (()=>{})});
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id==='schedule-mode'?'all':id==='schedule-span'?'4':'',
      textContent:'',hidden:false,style:{},handlers:{},
      parentElement:{clientWidth:800},
      addEventListener(name,fn){this.handlers[name]=fn;},
      getContext(){return context;}, getBoundingClientRect(){return {left:0,top:0,width:800};},
      replaceChildren(){},add(){}
    });
    return elements.get(id);
  }
  const window = {devicePixelRatio:1};
  const sandbox = {window,document:{getElementById:element},Option:function(){},ResizeObserver:class{observe(){}}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../src/nadit_physical_compiler/visualization/web/dashboard.js'),'utf8'),sandbox);
  const trace = {duration_us:10,initial:{atoms:[{id:7,qubit:0}]}, steps:[{start_us:0,duration_us:10,actions:[
    {kind:'Move',duration_us:10,atoms:[7],index:0,source_ids:[],parameters:{aod_id:'a'}}
  ]}],noise_report:{model:'noise',version:'v1.1',params:{},metrics:{fidelity:0.8},noise:{channels:[],gate_diagnostics:[],loss:{events:[{atom:7,start_us:0,duration_us:10,survival_probability:0.8}]}}}};
  let seeked, selected;
  const dashboard = new window.AtomReplayDashboard.Dashboard(t=>seeked=t,a=>selected=a);
  dashboard.load(trace,new Analysis(trace));
  dashboard.update(10,7,1);
  assert.equal(element('risk-now').textContent,'0.2');
  assert.equal(element('survival-now').textContent,'80%');
  assert.equal(element('atom-risk').textContent,'20%');
  element('schedule-canvas').handlers.click({clientX:400,clientY:30});
  assert.equal(seeked,0);assert.equal(selected,7);
  assert.match(element('schedule-detail').textContent,/Move/);
  dashboard.update(0,null,0);
  assert.equal(element('risk-now').textContent,'0');
  element('schedule-mode').value='follow';dashboard.update(5,null,0);
  assert.equal(dashboard.range[1]-dashboard.range[0],4);
  delete trace.noise_report;
  dashboard.load(trace,new Analysis(trace));dashboard.update(0,null,0);
  assert.equal(element('risk-content').hidden,true);
  assert.equal(element('risk-now').textContent,'—');
});
