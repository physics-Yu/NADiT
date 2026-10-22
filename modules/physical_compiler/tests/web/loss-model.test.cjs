const test = require('node:test');
const assert = require('node:assert/strict');
const {LossIndex, SOURCES} = require('../../src/nadit_physical_compiler/visualization/web/loss.js');
const near = (a,b) => assert.ok(Math.abs(a-b)<1e-12, `${a} != ${b}`);
const event = (atom,end,p,cause) => ({atom,start_us:0,duration_us:end,survival_probability:1-p,cause});
const sum = object => Object.values(object).reduce((a,b)=>a+b,0);

test('source contributions include prior survival and average over every input atom', () => {
  const model = new LossIndex([7,9], [event(7,1,.1,'load'), event(7,2,.2,'move'), event(7,3,.1,'store'),event(9,2,.1,'holder_lifetime')]);
  const risk = model.at(3);
  near(risk.expected,.452);near(risk.average,.226);
  assert.equal(risk.maximum.atom,7);near(risk.maximum.probability,.352);
  near(risk.atomContributions.get(7).load,.1);
  near(risk.atomContributions.get(7).move,.18);
  near(risk.atomContributions.get(7).store,.072);
  near(risk.averageContributions.residence,.05);
  for (const source of SOURCES) near(risk.averageContributions[source]*2,risk.contributions[source]);
  near(sum(risk.contributions),risk.expected);near(sum(risk.averageContributions),risk.average);
  near(sum(risk.atomContributions.get(7)),risk.maximum.probability);
  near(model.at(1).average,.05);near(model.at(0).expected,0);
  assert.equal(model.at(0).maximum.atom,null);
});

test('simultaneous sources share loss by hazard, independently of input order', () => {
  const events = [event(7,1,.1,'load'),event(7,2,.2,'move'),event(7,2,.3,'holder_lifetime')];
  const a = new LossIndex([7],events).at(2), b = new LossIndex([7],[...events].reverse()).at(2);
  near(a.expected,.496);
  near(a.contributions.move,.9*(1-.8*.7)*(-Math.log(.8))/(-Math.log(.8*.7)));
  for (const source of SOURCES) near(a.contributions[source],b.contributions[source]);
  near(sum(a.contributions),a.expected);
});

test('certain loss and zero noise remain finite; ties use the lowest atom ID', () => {
  const events = [event(9,1,1,'move'),event(9,1,1,'holder_lifetime'),event(9,1,.2,'load'),event(7,2,1,'store')];
  const model = new LossIndex([9,7],events);
  const risk = model.at(2);
  near(risk.expected,2);near(risk.average,1);
  near(risk.atomContributions.get(9).move,.5);near(risk.atomContributions.get(9).residence,.5);
  near(risk.atomContributions.get(9).load,0);
  assert.equal(risk.maximum.atom,7);assert.equal(risk.maximum.ties,2);
  near(sum(risk.contributions),2);
  assert.equal(model.at(1).maximum.atom,9);
  assert.equal(new LossIndex([],[]).at(0).average,0);
});

test('CZ, unclassified and legacy kinds are retained without folding leakage into loss', () => {
  const model = new LossIndex([7],[event(7,1,.1,'configured_cz_atom_loss'),event(7,2,.1,'custom'),{...event(7,3,.1,undefined),kind:'Load'}]);
  const risk=model.at(3);
  near(risk.contributions.cz,.1);near(risk.contributions.other,.09);near(risk.contributions.load,.081);
  near(sum(risk.contributions),risk.expected);
});
