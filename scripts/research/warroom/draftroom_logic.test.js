// node --test draftroom_logic.test.js   (written before the refactor: TDD)
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("./draftroom_logic.js");

const MY = [1, 24, 25, 48, 49, 72, 73, 96, 97, 120, 121, 144, 145, 168, 169];
const SLOTS = [["QB",1],["RB",2],["WR",2],["TE",1],["FLEX",2],["K",1],["DEF",1],["BN",5]];
const P = (id, pos, proj, extra={}) => ({id, name:id, pos, proj, vor:proj-100, adp:50, adp_sd:6, team:"X", ...extra});

test("Phi matches the normal CDF", () => {
  assert.ok(Math.abs(L.Phi(0) - 0.5) < 1e-6);
  assert.ok(Math.abs(L.Phi(1.96) - 0.975) < 1e-3);
  assert.ok(Math.abs(L.Phi(-3) - 0.00135) < 1e-4);
});

test("pAvail is a proper conditional survival probability", () => {
  assert.equal(L.pAvail({adp:30, adp_sd:4}, 25, 25), 1);
  const a = L.pAvail({adp:30, adp_sd:4}, 30, 25), b = L.pAvail({adp:30, adp_sd:4}, 48, 25);
  assert.ok(a > b && b >= 0 && a < 1);
  // fell far past ADP: taken immediately, no NaN
  const f = L.pAvail({adp:5, adp_sd:1.2}, 41, 40);
  assert.ok(f >= 0 && f < 0.05 && !Number.isNaN(f));
});

test("current pick counts logged picks plus unlogged offset", () => {
  assert.equal(L.curPick({picks:[], offset:0}), 1);
  assert.equal(L.curPick({picks:[{id:"a"},{id:"b"}], offset:0}), 3);
  assert.equal(L.curPick({picks:[{id:"a"}], offset:5}), 7);
});

test("nextMine finds the next of my picks at or after the current pick", () => {
  assert.equal(L.nextMine({picks:[], offset:0}, MY), 1);
  assert.equal(L.nextMine({picks:[{id:"a"}], offset:0}, MY), 24);
  assert.equal(L.nextMine({picks:new Array(24).fill({id:"x"}), offset:0}, MY), 25);
  assert.equal(L.nextMine({picks:new Array(180).fill({id:"x"}), offset:0}, MY), null);
});

test("lineup fills FLEX with the best leftover RB/WR/TE and benches the rest", () => {
  const roster = [P("qb","QB",300), P("rb1","RB",100), P("rb2","RB",90), P("rb3","RB",85), P("wr1","WR",95),
                  P("wr2","WR",70), P("te","TE",60), P("wr3","WR",88), P("k","K",100), P("def","DEF",90), P("rb4","RB",40)];
  const L1 = L.lineup(roster, SLOTS);
  assert.deepEqual(L1.FLEX.map(p=>p.id), ["rb3","wr2"]);
  assert.deepEqual(L1.BN.map(p=>p.id), ["rb4"]);
  assert.equal(L.lineupTotal(L1, SLOTS), 300+100+90+95+88+60+85+70+100+90);
});

test("filterSort hides drafted by default, keeps them when searching, honors tag filter", () => {
  const players = [P("a","RB",150,{tag:"usage"}), P("b","WR",140), P("c","TE",130,{tag:"avoid"})];
  const state = {picks:[{id:"b", mine:false}], offset:0};
  const base = {filterPos:"ALL", filterTag:null, sortKey:"vor", sortDir:-1, query:"", hideGone:true};
  assert.deepEqual(L.filterSort(players, state, MY, base).map(x=>x.p.id), ["a","c"]);
  assert.deepEqual(L.filterSort(players, state, MY, {...base, query:"b"}).map(x=>x.p.id), ["b"]);
  assert.deepEqual(L.filterSort(players, state, MY, {...base, filterTag:"avoid"}).map(x=>x.p.id), ["c"]);
  assert.deepEqual(L.filterSort(players, state, MY, {...base, filterPos:"WR", hideGone:false}).map(x=>x.p.id), ["b"]);
  const asc = L.filterSort(players, state, MY, {...base, sortDir:1});
  assert.deepEqual(asc.map(x=>x.p.id), ["c","a"]);
});

test("reset machine: first tap arms, second tap fires, timeout disarms", () => {
  let fired = 0; let timers = [];
  const m = L.resetMachine(() => fired++, {setTimeout:(fn)=>{timers.push(fn); return timers.length;}, clearTimeout:()=>{}});
  assert.equal(m.tap(), "armed");
  assert.equal(m.tap(), "fired");
  assert.equal(fired, 1);
  assert.equal(m.tap(), "armed");
  timers[timers.length-1]();            // simulate the 4 s expiry
  assert.equal(m.armed(), false);
  assert.equal(m.tap(), "armed");       // needs two taps again
  assert.equal(fired, 1);
});
