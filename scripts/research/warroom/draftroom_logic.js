// Pure logic for the 1.01 War Room. Inlined into the page at build time;
// also loaded by node for draftroom_logic.test.js. Mirrors gridiron/draft.py.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.DraftLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  // Abramowitz–Stegun normal CDF (abs err < 7.5e-8)
  function Phi(z) {
    const t = 1 / (1 + 0.2316419 * Math.abs(z));
    const d = 0.3989423 * Math.exp(-z * z / 2);
    const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
    return z > 0 ? 1 - p : p;
  }
  const pUncond = (p, pick) => 1 - Phi((pick - 0.5 - (p.adp_model ?? p.adp)) / p.adp_sd);
  // P(still there at `pick` | still there at `now`), ADP model
  function pAvailAdp(p, pick, now) {
    if (pick <= now) return 1;
    const pn = pUncond(p, now);
    if (pn <= 1e-12) return 0;
    return Math.max(0, Math.min(1, pUncond(p, pick) / pn));
  }
  // Pre-draft neutral-room snapshot. It is NOT conditioned on actual picks.
  function pAvailRoom(p, pick, now, MY) {
    if (pick <= now) return 1;
    if (p.ph && Number.isFinite(p.ph[pick])) return Math.max(0, Math.min(1, p.ph[pick]));
    return pAvailAdp(p, pick, now);
  }
  // opts.mode: "room" (default) | "adp" | "hybrid" (w = weight on the room, default 0.5)
  function pAvail(p, pick, now, MY, opts) {
    // At consecutive user picks no opponent can remove a passed player.
    if (MY && MY.includes(now) && pick === now + 1 && MY.includes(pick)) return 1;
    const mode = (opts && opts.mode) || "room";
    if (mode === "adp") return pAvailAdp(p, pick, now);
    if (mode === "hybrid") {
      const w = opts.w == null ? 0.5 : Math.max(0, Math.min(1, opts.w));
      return w * pAvailRoom(p, pick, now, MY) + (1 - w) * pAvailAdp(p, pick, now);
    }
    return pAvailRoom(p, pick, now, MY);
  }
  const curPick = (state) => state.picks.length + (state.offset || 0) + 1;
  function nextMine(state, MY) {
    const c = curPick(state);
    const n = MY.find((x) => x >= c);
    return n === undefined ? null : n;
  }
  // The pick the survival odds should refer to: when I am on the clock the
  // question is "if I pass on him now, is he there at my NEXT pick".
  function nextTarget(state, MY) {
    const c = curPick(state);
    const n = MY.find((x) => x > c);
    return n === undefined ? null : n;
  }
  const FLEXABLE = ["RB", "WR", "TE"];
  // Greedy optimal lineup: fixed slots first, then FLEX from the best leftovers.
  function lineup(players, SLOTS) {
    const used = new Set();
    const out = {};
    for (const [pos, n] of SLOTS) {
      if (pos === "FLEX" || pos === "BN") continue;
      const c = players.filter((p) => p.pos === pos && !used.has(p)).sort((a, b) => b.proj - a.proj).slice(0, n);
      c.forEach((p) => used.add(p));
      out[pos] = c;
    }
    const nf = (SLOTS.find((s) => s[0] === "FLEX") || [0, 0])[1];
    out.FLEX = players.filter((p) => FLEXABLE.includes(p.pos) && !used.has(p)).sort((a, b) => b.proj - a.proj).slice(0, nf);
    out.FLEX.forEach((p) => used.add(p));
    out.BN = players.filter((p) => !used.has(p)).sort((a, b) => b.proj - a.proj);
    return out;
  }
  function lineupTotal(L, SLOTS) {
    let t = 0;
    for (const [pos] of SLOTS) if (pos !== "BN") for (const p of L[pos] || []) t += p.proj;
    return t;
  }
  const TAG_SETS = { sleepers: ["usage", "experts"], avoid: ["avoid", "regress"] };
  // Returns [{p, gone, mine, pnext}] filtered and sorted. A search ignores hideGone.
  function filterSort(players, state, MY, o) {
    const c = curPick(state), nm = nextTarget(state, MY);
    const taken = new Map(state.picks.map((x) => [x.id, !!x.mine]));
    const q = (o.query || "").trim().toLowerCase();
    const oo = { mode: o.mode, w: o.w };
    let list = players.map((p) => ({ p, gone: taken.has(p.id), mine: taken.get(p.id) === true, pnext: nm ? pAvail(p, nm, c, MY, oo) : 0 }));
    if (o.filterPos && o.filterPos !== "ALL") list = list.filter((x) => x.p.pos === o.filterPos);
    if (o.filterTag) list = list.filter((x) => TAG_SETS[o.filterTag].includes(x.p.tag));
    if (q) list = list.filter((x) => x.p.name.toLowerCase().includes(q) || (x.p.team || "").toLowerCase() === q);
    else if (o.hideGone) list = list.filter((x) => !x.gone);
    const dir = o.sortDir || -1, k = o.sortKey || "vor";
    const key = (x) => (k === "pnext" ? x.pnext : (x.p[k] == null ? (dir < 0 ? -1e9 : 1e9) : x.p[k]));
    list.sort((a, b) => (key(a) - key(b)) * dir);
    return list;
  }
  // Two-tap confirm; injectable timers for tests.
  function resetMachine(onFire, t) {
    t = t || { setTimeout: (f, ms) => setTimeout(f, ms), clearTimeout: (h) => clearTimeout(h) };
    let handle = null;
    const disarm = () => { handle = null; };
    return {
      armed: () => handle !== null,
      tap() {
        if (handle !== null) { t.clearTimeout(handle); handle = null; onFire(); return "fired"; }
        handle = t.setTimeout(disarm, 4000);
        return "armed";
      },
    };
  }
  function validateState(value) {
    if (!value || typeof value !== "object" || !Array.isArray(value.picks)) throw Error("Invalid draft state");
    const offset = value.offset == null ? 0 : value.offset;
    if (!Number.isInteger(offset) || offset < 0 || offset + value.picks.length > 180) throw Error("Invalid pick offset");
    const seen = new Set();
    const picks = value.picks.map(x => {
      if (!x || (x.id !== null && (typeof x.id !== "string" || !x.id.length)) || typeof x.mine !== "boolean")
        throw Error("Invalid pick entry");
      if (x.id && seen.has(x.id)) throw Error("Invalid duplicate player");
      if (x.id) seen.add(x.id);
      return {...x};
    });
    return {...value, picks, offset,
      mode:["adp","room","hybrid"].includes(value.mode) ? value.mode : "adp",
      w: typeof value.w === "number" && value.w >= 0 && value.w <= 1 ? value.w : 0.5};
  }

  function replacePick(state, index, entry, MY) {
    const next = validateState(state);
    if (!Number.isInteger(index) || index < 0 || index > next.picks.length ||
        next.offset + index >= 180) throw Error("Invalid pick number");
    if (entry.id && next.picks.some((x,i)=>i!==index && x.id===entry.id)) throw Error("Player already drafted");
    if (entry.mine !== MY.includes(index + next.offset + 1)) throw Error("Wrong turn: check Mine/Gone and the pick counter");
    next.picks[index] = {...entry};
    return validateState(next);
  }

  function persistState(storage, key, state) {
    try { storage.setItem(key, JSON.stringify(validateState(state))); return {ok:true}; }
    catch (error) { return {ok:false, error:String(error.message || error)}; }
  }

  function bestFlex(players, picks) {
    const taken = new Set(picks.map(p=>p.id));
    return players.filter(p=>FLEXABLE.includes(p.pos)&&!p.excluded&&!taken.has(p.id))
      .sort((a,b)=>b.proj-a.proj || String(a.id).localeCompare(String(b.id)))[0] || null;
  }
  function draftSlot(pick) {const round=Math.floor((pick-1)/12);const col=(pick-1)%12+1;return round%2?13-col:col;}
  function sleeperPicks(rows, config) {
    if(!Array.isArray(rows)||rows.length>180)throw Error("Invalid Sleeper pick list");
    const seen=new Set();
    return [...rows].sort((a,b)=>a.pick_no-b.pick_no).map((r,i)=>{
      if(r.pick_no!==i+1||r.draft_id!==config.draftId||typeof r.player_id!=="string"||!r.player_id||seen.has(r.player_id))throw Error("Incomplete or duplicate Sleeper picks; preserving current board");
      const slot=draftSlot(i+1), roster=Number(r.roster_id ?? config.slotToRoster[slot]);
      if(Number(r.draft_slot)!==slot||roster!==Number(config.slotToRoster[slot]))throw Error("Traded or changed draft order is unsupported; use manual mode");
      seen.add(r.player_id);
      return {id:r.player_id,mine:roster===config.rosterId,rosterId:roster,draftSlot:slot,source:"sleeper",metadata:r.metadata||{}};
    });
  }
  return { bestFlex, draftSlot, sleeperPicks, validateState, replacePick, persistState, Phi, pAvail, pAvailAdp, pAvailRoom, curPick, nextMine, nextTarget, lineup, lineupTotal, filterSort, resetMachine, TAG_SETS };
});
