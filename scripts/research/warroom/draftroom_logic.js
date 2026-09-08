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
  const pUncond = (p, pick) => 1 - Phi((pick - 0.5 - p.adp) / p.adp_sd);
  // P(still there at `pick` | still there at `now`)
  function pAvail(p, pick, now) {
    if (pick <= now) return 1;
    const pn = pUncond(p, now);
    if (pn <= 1e-12) return 0;
    return Math.max(0, Math.min(1, pUncond(p, pick) / pn));
  }
  const curPick = (state) => state.picks.length + (state.offset || 0) + 1;
  function nextMine(state, MY) {
    const c = curPick(state);
    const n = MY.find((x) => x >= c);
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
    const c = curPick(state), nm = nextMine(state, MY);
    const taken = new Map(state.picks.map((x) => [x.id, !!x.mine]));
    const q = (o.query || "").trim().toLowerCase();
    let list = players.map((p) => ({ p, gone: taken.has(p.id), mine: taken.get(p.id) === true, pnext: nm ? pAvail(p, nm, c) : 0 }));
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
  return { Phi, pAvail, curPick, nextMine, lineup, lineupTotal, filterSort, resetMachine, TAG_SETS };
});
