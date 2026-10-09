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

test("pAvail prefers simulated survival odds (ph) when the player has them", () => {
  // ph: chance still there at each of MY picks from the history-aware sim
  const p = {adp:20, adp_sd:3, ph:{1:1, 24:0.4, 25:0.3, 48:0.02}};
  // before my first pick: use ph directly
  assert.equal(L.pAvail(p, 24, 1, MY), 0.4);
  // at consecutive user picks nobody else can select the player
  assert.equal(L.pAvail(p, 25, 24, MY), 1); // consecutive picks: no opponent acts
  // mid-round: retain the unconditioned room snapshot; no fabricated live conditioning
  assert.equal(L.pAvail(p, 48, 30, MY), 0.02); // explicitly a pre-draft snapshot
  // zero snapshots are retained as scenarios, with consecutive picks handled exactly
  const q = {adp:5, adp_sd:1.2, ph:{1:1, 24:0.0, 25:0.0}};
  const f = L.pAvail(q, 25, 24, MY);
  assert.ok(f >= 0 && f <= 1 && !Number.isNaN(f));
  // no ph: unchanged ADP model
  assert.equal(L.pAvail({adp:30, adp_sd:4}, 25, 25), 1);
});

test("odds mode: adp ignores the room, room uses it, hybrid blends by weight", () => {
  const p = {adp:20, adp_sd:3, ph:{1:1, 24:0.4, 25:0.3, 48:0.02}};
  const adp = L.pAvail(p, 24, 1, MY, {mode:"adp"});
  const room = L.pAvail(p, 24, 1, MY, {mode:"room"});
  assert.ok(Math.abs(adp - L.pAvailAdp(p, 24, 1)) < 1e-12);   // pure market model
  assert.equal(room, 0.4);                                    // pure history
  const hy = L.pAvail(p, 24, 1, MY, {mode:"hybrid", w:0.25});  // 25% room, 75% market
  assert.ok(Math.abs(hy - (0.25*0.4 + 0.75*adp)) < 1e-12);
  assert.ok(Math.abs(L.pAvail(p, 24, 1, MY, {mode:"hybrid", w:1}) - room) < 1e-12);
  assert.ok(Math.abs(L.pAvail(p, 24, 1, MY, {mode:"hybrid", w:0}) - adp) < 1e-12);
  // default (no options) stays the room model with ADP fallback
  assert.equal(L.pAvail(p, 24, 1, MY), 0.4);
  // filterSort passes the mode through
  const players = [{...p, id:"a", name:"a", pos:"RB", proj:150, vor:50, team:"X"}];
  const s = {picks:[], offset:0};
  assert.ok(Math.abs(L.filterSort(players, s, MY, {mode:"adp"})[0].pnext - adp) < 1e-12);
  assert.ok(Math.abs(L.filterSort(players, s, MY, {mode:"hybrid", w:0.5})[0].pnext - (0.5*0.4+0.5*adp)) < 1e-12);
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

test("survival odds refer to my next pick AFTER the current one when I am on the clock", () => {
  assert.equal(L.nextTarget({picks:[], offset:0}, MY), 24);          // on the clock at 1 -> odds at 24
  assert.equal(L.nextTarget({picks:new Array(23).fill({id:"x"}), offset:0}, MY), 25);   // on the clock at 24 -> 25
  assert.equal(L.nextTarget({picks:new Array(30).fill({id:"x"}), offset:0}, MY), 48);   // mid-round at 31 -> 48
  assert.equal(L.nextTarget({picks:new Array(169).fill({id:"x"}), offset:0}, MY), null);
  const players = [P("a","RB",150,{ph:{1:1,24:0.4,25:0.4,48:0.1}})];
  const atOne = L.filterSort(players, {picks:[], offset:0}, MY, {sortKey:"vor", sortDir:-1, hideGone:true});
  assert.ok(Math.abs(atOne[0].pnext - 0.4) < 1e-9);
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


test("correcting an old pick preserves later picks and the clock", () => {
  const s = {picks:[{id:"a",mine:true},{id:"b",mine:false},{id:"c",mine:false}],offset:0};
  const n = L.replacePick(s,1,{id:"d",mine:false},MY);
  assert.equal(L.curPick(n),4);
  assert.deepEqual(n.picks.map(x=>x.id),["a","d","c"]);
  assert.deepEqual(s.picks.map(x=>x.id),["a","b","c"]);
  const cleared = L.replacePick(n,1,{id:null,mine:false},MY);
  assert.equal(L.curPick(cleared),4);
  assert.equal(cleared.picks[1].id,null);
});

test("pick recording rejects duplicates, wrong ownership, and out-of-range edits", () => {
  const s={picks:[{id:"a",mine:true}],offset:0};
  assert.throws(()=>L.replacePick(s,1,{id:"a",mine:false},MY),/already/);
  assert.throws(()=>L.replacePick(s,1,{id:"b",mine:true},MY),/turn/);
  assert.throws(()=>L.replacePick(s,4,{id:"b",mine:false},MY),/pick/);
  assert.equal(L.replacePick(s,1,{id:"b",mine:false},MY).picks.length,2);
});

test("backup validation rejects corrupt input without mutating the live state", () => {
  assert.throws(()=>L.validateState(null),/state/);
  assert.throws(()=>L.validateState({picks:[{id:"a",mine:true},{id:"a",mine:false}]}),/duplicate/);
  assert.throws(()=>L.validateState({picks:[],offset:-2}),/offset/);
  assert.equal(L.validateState({picks:[],offset:2}).offset,2);
});

test("save errors are observable and successful storage round-trips", () => {
  const s={picks:[{id:"a",mine:true}],offset:0};
  const broken={setItem(){throw Error("quota");}};
  assert.equal(L.persistState(broken,"k",s).ok,false);
  const storage={setItem(k,v){this[k]=v;}};
  assert.equal(L.persistState(storage,"k",s).ok,true);
  assert.equal(L.validateState(JSON.parse(storage.k)).picks[0].id,"a");
});

test("no opponent pick occurs between my consecutive turn selections", () => {
  const p=P("pass","RB",120,{adp:10,adp_sd:2,ph:{25:0}});
  for(const mode of ["adp","room","hybrid"])
    assert.equal(L.pAvail(p,25,24,MY,{mode,w:0.5}),1);
});

test('FLEX uses projected points across RB WR TE and excludes drafted or excluded players',()=>{
  const p=[{id:'q',pos:'QB',proj:400},{id:'r',pos:'RB',proj:250,vor:100},{id:'w',pos:'WR',proj:260,vor:80},{id:'t',pos:'TE',proj:300,excluded:true}];
  assert.equal(L.bestFlex(p,[]).id,'w');
  assert.equal(L.bestFlex(p,[{id:'w'}]).id,'r');
  assert.equal(L.bestFlex(p,[{id:'w'},{id:'r'}]),null);
});
test('Sleeper picks preserve roster ownership including autopicks',()=>{
  const cfg={draftId:'d',rosterId:3,slotToRoster:{1:3,2:9}};
  const picks=[{draft_id:'d',pick_no:2,player_id:'b',draft_slot:2,roster_id:9,picked_by:''},{draft_id:'d',pick_no:1,player_id:'a',draft_slot:1,roster_id:3,picked_by:''}];
  const actual=L.sleeperPicks(picks,cfg);
  assert.equal(actual[0].mine,true);assert.equal(actual[1].mine,false);assert.equal(actual[1].rosterId,9);
  assert.equal(L.draftSlot(24),1);assert.equal(L.draftSlot(25),1);
});
test('Sleeper sync rejects missing pick numbers, duplicates, wrong draft and traded ownership',()=>{
  const cfg={draftId:'d',rosterId:3,slotToRoster:{1:3,2:9}};
  const p={draft_id:'d',pick_no:1,player_id:'a',draft_slot:1,roster_id:3};
  for(const bad of [{...p,pick_no:2},{...p,draft_id:'other'},{...p,roster_id:9}])assert.throws(()=>L.sleeperPicks([bad],cfg));
  assert.throws(()=>L.sleeperPicks([p,{...p,pick_no:2,draft_slot:2,roster_id:9}],cfg));
});
test('Missing displayed ADP uses separately labeled model value for odds',()=>{
  assert.equal(L.pAvailAdp({adp:null,adp_model:50,adp_sd:10},60,20),L.pAvailAdp({adp:50,adp_sd:10},60,20));
});
