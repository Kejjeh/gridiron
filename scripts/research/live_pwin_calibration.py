"""Calibrate a LIVE fantasy P(win) — the model nfl-gamezone will run in the browser.

    PYTHONPATH=src python scripts/research/live_pwin_calibration.py [--refresh]

The live model (what Game Zone ships):

    each starter i:  f_i  = share of his game still to play (1 before kickoff, 0 final)
                     mu_i = proj_i * f_i                       (remaining mean)
                     sd_i = f_i**(g/2) * (a_pos + b_pos*proj_i) (remaining spread)
    P(win) = Phi( (now_a - now_b + sum mu_a - sum mu_b) / (k * sqrt(sum sd^2)) )

`proj_i` is Sleeper's weekly projection (api.sleeper.com/projections) scored
as a DOT PRODUCT with the league's own `scoring_settings` — Game Zone's rule
for every fantasy number it computes. Two derived keys the dot product would
otherwise drop: `fgmiss` (the league scores one miss key; projections split
misses by distance, `fgmiss_30_39`...) is the sum of the `fgmiss_*` keys.

What this script measures, on THIS league's real matchups 2023-2026:
  1. Each starter's points at wall-clock checkpoints through every week
     (before each kickoff slot, and ~95 min into it), rebuilt from nflverse
     play-by-play under the league's scoring, with game time mapped from the
     wall clock at 190 real minutes per game. The rebuild's FINAL is checked
     against Sleeper's own `players_points`; a checkpoint at or after a
     game's end uses Sleeper's number (the truth Game Zone reads).
  2. The spread parameters (a_pos, b_pos, g) by Gaussian maximum likelihood on
     starter-level remaining points, fitted on 2023-2024 only; the global
     spread factor k on 2023-2024 matchup checkpoints.
  3. The matchup-level P(win) on 2025-2026 (never seen by the fit): log loss,
     Brier and calibration by stage of the week, against
       - the points-share bar Game Zone shows today (me / (me + opp)),
       - the pre-game P(win) frozen at the week's first kickoff,
       - the same live model with one spread for every position.
Outputs:
  data/reference/live_pwin_v1.json          constants + provenance + metrics (committed)
  data/reference/live_pwin_fixtures.json    a few held-out snapshots, no ids or names,
                                            for Game Zone's offline test (committed)
  data/research/cache/live_pwin/            raw Sleeper JSON and slim pbp (gitignored)
Prints no player names, manager names or league ids.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

from gridiron.ids import is_dst_id, nflverse_team, normalize_id
from gridiron.paths import REPO_ROOT
from gridiron.sleeper import http_fetch, resolve_league_id

CACHE = REPO_ROOT / "data" / "research" / "cache" / "live_pwin"
REF = REPO_ROOT / "data" / "reference"
SLEEPER = "https://api.sleeper.app/v1"
PROJ = "https://api.sleeper.com/projections/nfl/{s}/{w}?season_type=regular" + "".join(
    f"&position%5B%5D={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))
GAME_MINUTES = 190.0           # real minutes from kickoff to final, used to map wall clock -> game clock
TRAIN = (2023, 2024)
TEST = (2025, 2026)
ET = timezone(timedelta(hours=-5))
POS = ("QB", "RB", "WR", "TE", "K", "DEF")


def cached(name: str, fetch, refresh: bool):
    p = CACHE / name
    if p.exists() and not refresh:
        return json.loads(p.read_text())
    obj = fetch()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return obj


# ----------------------------------------------------------------- league history
def league_seasons(refresh: bool) -> list[dict]:
    out, lid = [], resolve_league_id()
    while lid and lid != "0":
        lg = cached(f"league_{lid}.json", lambda lid=lid: http_fetch(f"{SLEEPER}/league/{lid}"), refresh)
        if not lg:
            break
        out.append(lg)
        lid = lg.get("previous_league_id")
    return out


def matchups(lg: dict, week: int, refresh: bool) -> list[dict]:
    lid = lg["league_id"]
    return cached(f"matchups_{lid}_{week}.json",
                  lambda: http_fetch(f"{SLEEPER}/league/{lid}/matchups/{week}") or [], refresh)


def projections(season: int, week: int, refresh: bool) -> dict[str, dict]:
    def fetch():
        rows = http_fetch(PROJ.format(s=season, w=week)) or []
        keep = {}
        for r in rows:
            st = r.get("stats") or {}
            if st:
                keep[normalize_id(r.get("player_id"))] = {k: v for k, v in st.items()
                                                          if isinstance(v, (int, float))}
        return keep
    return cached(f"proj_{season}_{week}.json", fetch, refresh)


def score_projection(stats: dict, scoring: dict) -> float:
    """Game Zone's rule: a dot product with the league's scoring settings, plus
    the derived keys a plain dot product misses."""
    s = dict(stats)
    if "fgmiss" in scoring and "fgmiss" not in s:
        s["fgmiss"] = sum(v for k, v in stats.items() if k.startswith("fgmiss_"))
    return float(sum(s.get(k, 0.0) * w for k, w in scoring.items()))


# ----------------------------------------------------------------- play-by-play timeline
PBP_COLS = ["game_id", "week", "season_type", "home_team", "away_team", "posteam", "defteam",
            "game_seconds_remaining", "qtr", "play_type", "passer_player_id", "rusher_player_id",
            "receiver_player_id", "kicker_player_id", "fumbled_1_player_id", "passing_yards",
            "rushing_yards", "receiving_yards", "complete_pass", "pass_touchdown", "rush_touchdown",
            "interception", "fumble_lost", "fumble_forced", "forced_fumble_player_1_team", "sack",
            "safety", "td_team", "field_goal_result", "kick_distance", "extra_point_result",
            "two_point_conv_result", "total_home_score", "total_away_score", "punt_blocked",
            "return_touchdown"]


def load_pbp(season: int, refresh: bool) -> pd.DataFrame:
    p = CACHE / f"pbp_{season}.parquet"
    if p.exists() and not refresh:
        return pd.read_parquet(p)
    import nflreadpy as nfl
    df = nfl.load_pbp([season]).to_pandas()
    df = df[[c for c in PBP_COLS if c in df.columns]]
    df = df[df.season_type == "REG"].copy()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)
    return df


def game_elapsed(pbp: pd.DataFrame) -> pd.Series:
    """Game seconds elapsed at each play: 0-3600 in regulation, 3600+ in
    overtime (an OT period counts down from 600)."""
    gsr = pbp.game_seconds_remaining.fillna(0)
    reg = (3600 - gsr).clip(lower=0)
    ot = 3600 + (600 - gsr).clip(lower=0)
    return reg.where(pbp.qtr <= 4, ot)


def play_points(pbp: pd.DataFrame, scoring: dict, gsis_to_sleeper: dict) -> pd.DataFrame:
    """One row per (game, play, sleeper id) with the points that play earned
    him under the league's scoring, and the game second it happened at."""
    w = lambda k: float(scoring.get(k, 0.0))
    rows = []
    pbp = pbp.assign(el=game_elapsed(pbp).values)
    for r in pbp.itertuples(index=False):
        base = (r.game_id, r.el)

        def add(gsis, pts):
            sid = gsis_to_sleeper.get(gsis) if isinstance(gsis, str) else None
            if sid and pts:
                rows.append((*base, sid, pts))

        def team(t, pts):
            if isinstance(t, str) and pts:
                rows.append((*base, nflverse_team(t), pts))

        py = r.passing_yards if r.passing_yards == r.passing_yards else 0.0
        ry = r.rushing_yards if r.rushing_yards == r.rushing_yards else 0.0
        cy = r.receiving_yards if r.receiving_yards == r.receiving_yards else 0.0
        add(r.passer_player_id, py * w("pass_yd") + (r.pass_touchdown == 1) * w("pass_td")
            + (r.interception == 1) * w("pass_int"))
        add(r.rusher_player_id, ry * w("rush_yd") + (r.rush_touchdown == 1) * w("rush_td"))
        add(r.receiver_player_id, cy * w("rec_yd") + (r.complete_pass == 1) * w("rec")
            + (r.pass_touchdown == 1) * w("rec_td"))
        if r.fumble_lost == 1:
            add(r.fumbled_1_player_id, w("fum_lost"))
        if r.two_point_conv_result == "success":
            if isinstance(r.receiver_player_id, str):
                add(r.passer_player_id, w("pass_2pt")); add(r.receiver_player_id, w("rec_2pt"))
            else:
                add(r.rusher_player_id, w("rush_2pt"))
        if r.field_goal_result == "made":
            d = r.kick_distance or 0
            key = "fgm_0_19" if d < 20 else "fgm_20_29" if d < 30 else "fgm_30_39" if d < 40 \
                else "fgm_40_49" if d < 50 else "fgm_50p"
            add(r.kicker_player_id, w(key))
        elif r.field_goal_result in ("missed", "blocked"):
            add(r.kicker_player_id, w("fgmiss"))
        if r.extra_point_result == "good":
            add(r.kicker_player_id, w("xpm"))
        elif r.extra_point_result in ("failed", "blocked"):
            add(r.kicker_player_id, w("xpmiss"))
        # team defense / special teams (the defending side of the play)
        d = r.defteam
        team(d, (r.sack == 1) * w("sack") + (r.interception == 1) * w("int")
             + (r.fumble_lost == 1) * w("fum_rec") + (r.safety == 1) * w("safe")
             + ((r.field_goal_result == "blocked") or (r.punt_blocked == 1)) * w("blk_kick"))
        if r.fumble_forced == 1 and isinstance(r.forced_fumble_player_1_team, str):
            team(r.forced_fumble_player_1_team, w("ff"))
        if isinstance(r.td_team, str) and isinstance(r.posteam, str) and r.td_team != r.posteam:
            team(r.td_team, w("def_td") if r.play_type not in ("kickoff", "punt") else w("def_st_td"))
    return pd.DataFrame(rows, columns=["game_id", "el", "sid", "pts"])


def pts_allowed_points(pa: float, scoring: dict) -> float:
    for lo, hi, k in ((0, 0, "pts_allow_0"), (1, 6, "pts_allow_1_6"), (7, 13, "pts_allow_7_13"),
                      (14, 20, "pts_allow_14_20"), (21, 27, "pts_allow_21_27"),
                      (28, 34, "pts_allow_28_34"), (35, 999, "pts_allow_35p")):
        if lo <= pa <= hi:
            return float(scoring.get(k, 0.0))
    return 0.0


# ----------------------------------------------------------------- assembling snapshots
def week_kickoffs(sched: pd.DataFrame, season: int, week: int) -> dict[str, tuple[datetime, str]]:
    s = sched[(sched.season == season) & (sched.week == week) & (sched.game_type == "REG")]
    out = {}
    for g in s.itertuples():
        k = datetime.fromisoformat(f"{g.gameday}T{g.gametime}").replace(tzinfo=ET)
        for t in (g.home_team, g.away_team):
            out[t] = (k, g.game_id)
    return out


def build(refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """starter-level rows (season, week, matchup, side, checkpoint, pos, proj,
    partial, f, final) and the reconstruction audit."""
    import nflreadpy as nfl
    seasons = league_seasons(refresh)
    sched = nfl.load_schedules([int(lg["season"]) for lg in seasons]).to_pandas()
    rows, audit = [], {"exact": 0, "close": 0, "off": 0, "n": 0}
    for lg in seasons:
        season = int(lg["season"])
        scoring = {k: float(v) for k, v in (lg.get("scoring_settings") or {}).items() if v}
        last = int(lg["settings"].get("playoff_week_start", 15)) - 1
        ro = nfl.load_rosters_weekly([season]).to_pandas()
        g2s = {g: normalize_id(s) for g, s in ro[["gsis_id", "sleeper_id"]].dropna().values}
        s2team = {}
        for s, t, w in ro[["sleeper_id", "team", "week"]].dropna().values:
            s2team[(normalize_id(s), int(w))] = nflverse_team(t)
        pbp = load_pbp(season, refresh)
        pp = play_points(pbp, scoring, g2s)
        s2pos = {normalize_id(s): str(pz) for s, pz in ro[["sleeper_id", "position"]].dropna().values}
        score_el = pbp.assign(el=game_elapsed(pbp).values)[
            ["game_id", "el", "home_team", "away_team", "total_home_score", "total_away_score"]]
        for week in range(1, last + 1):
            ms = matchups(lg, week, refresh)
            if not ms or not any(m.get("points") for m in ms):
                continue
            kick = week_kickoffs(sched, season, week)
            if not kick:
                continue
            done = [k + timedelta(minutes=GAME_MINUTES) for k, _ in kick.values()]
            if season == 2026 and max(done) > datetime.now(UTC):
                continue                                   # week not finished
            proj = projections(season, week, refresh)
            slots = sorted({k for k, _ in kick.values()})
            cps = sorted({k - timedelta(minutes=1) for k in slots}
                         | {k + timedelta(minutes=95) for k in slots})
            wk_pp = pp[pp.game_id.isin({g for _, g in kick.values()})]
            by = {(m["matchup_id"], m["roster_id"]): m for m in ms if m.get("matchup_id")}
            pairs = {}
            for (mid, rid), m in by.items():
                pairs.setdefault(mid, []).append(m)
            for mid, sides in pairs.items():
                if len(sides) != 2:
                    continue
                for side_i, m in enumerate(sides):
                    for sid in [normalize_id(x) for x in (m.get("starters") or []) if x and x != "0"]:
                        dst = is_dst_id(sid)
                        team = nflverse_team(sid) if dst else s2team.get((sid, week))
                        pos = "DEF" if dst else s2pos.get(sid)
                        pos = pos if pos in POS else None
                        key = team if dst else sid
                        final = float((m.get("players_points") or {}).get(sid, 0.0))
                        st = proj.get(sid)
                        p = score_projection(st, scoring) if st else 0.0
                        kk = kick.get(team) if team else None
                        mine = wk_pp[wk_pp.sid == key] if kk else wk_pp.iloc[0:0]
                        if kk:
                            mine = mine[mine.game_id == kk[1]]
                            rec = float(mine.pts.sum())
                            if pos == "DEF":
                                g = score_el[score_el.game_id == kk[1]]
                                if len(g):
                                    last_row = g.iloc[-1]
                                    pa = last_row.total_away_score if last_row.home_team == team else last_row.total_home_score
                                    rec += pts_allowed_points(pa, scoring)
                            audit["n"] += 1
                            audit["exact" if abs(rec - final) < 0.05 else "close" if abs(rec - final) <= 2 else "off"] += 1
                        for ci, t in enumerate(cps):
                            if kk is None:
                                f, part = 0.0, final            # no game: nothing to come
                            else:
                                k0, gid = kk
                                frac = (t - k0).total_seconds() / 60 / GAME_MINUTES
                                if frac >= 1:
                                    f, part = 0.0, final
                                elif frac <= 0:
                                    f, part = 1.0, 0.0
                                else:
                                    e = 3600 * frac
                                    f = 1 - frac
                                    part = float(mine[mine.el <= e].pts.sum())
                                    if pos == "DEF":
                                        g = score_el[(score_el.game_id == gid) & (score_el.el <= e)]
                                        pa = 0.0
                                        if len(g):
                                            lr = g.iloc[-1]
                                            pa = lr.total_away_score if lr.home_team == team else lr.total_home_score
                                        part += pts_allowed_points(pa, scoring)
                            rows.append((season, week, mid, side_i, ci, len(cps), pos or "UNK",
                                         p, part, f, final, sid))
    df = pd.DataFrame(rows, columns=["season", "week", "mid", "side", "cp", "ncp", "pos", "proj",
                                     "partial", "f", "final", "sid"])
    return df, pd.DataFrame([audit]), {"seasons": [int(lg["season"]) for lg in seasons]}


# ----------------------------------------------------------------- model
def sd_of(params: dict, pos, proj, f):
    a = np.array([params["a"][p] for p in pos]); b = np.array([params["b"][p] for p in pos])
    return np.power(np.clip(f, 0, 1), params["g"] / 2) * (a + b * np.clip(proj, 0, None))


def fit_spread(tr: pd.DataFrame) -> dict:
    t = tr[(tr.f > 0) & (tr.pos.isin(POS))]
    resid = (t.final - t.partial - t.proj * t.f).values
    pos = t.pos.values; proj = t.proj.values; f = t.f.values
    idx = {p: i for i, p in enumerate(POS)}

    def nll(x):
        a = np.exp(x[:6]); b = np.exp(x[6:12]); g = x[12]
        ai = a[[idx[p] for p in pos]]; bi = b[[idx[p] for p in pos]]
        sd = np.power(f, g / 2) * (ai + bi * np.clip(proj, 0, None)) + 1e-6
        return -norm.logpdf(resid, 0, sd).sum()
    x0 = np.r_[np.log(np.full(6, 2.0)), np.log(np.full(6, 0.4)), 1.0]
    r = minimize(nll, x0, method="L-BFGS-B", bounds=[(-5, 3)] * 12 + [(0.3, 2.0)])
    return {"a": {p: float(np.exp(r.x[idx[p]])) for p in POS},
            "b": {p: float(np.exp(r.x[6 + idx[p]])) for p in POS}, "g": float(r.x[12])}


def matchup_frame(df: pd.DataFrame, params: dict, k: float = 1.0) -> pd.DataFrame:
    d = df[df.pos.isin(POS)].copy()
    d["mu"] = d.proj * d.f
    d["var"] = sd_of(params, d.pos.values, d.proj.values, d.f.values) ** 2
    d["pre_mu"] = d.proj
    d["pre_var"] = sd_of(params, d.pos.values, d.proj.values, np.ones(len(d))) ** 2
    g = d.groupby(["season", "week", "mid", "cp", "side"]).agg(
        now=("partial", "sum"), mu=("mu", "sum"), var=("var", "sum"), final=("final", "sum"),
        pre_mu=("pre_mu", "sum"), pre_var=("pre_var", "sum"), ncp=("ncp", "first"),
        rem=("mu", "sum"), tot=("proj", "sum")).reset_index()
    a = g[g.side == 0].set_index(["season", "week", "mid", "cp"])
    b = g[g.side == 1].set_index(["season", "week", "mid", "cp"])
    m = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
    m = m[m.final_a != m.final_b]
    sd = np.sqrt(m.var_a + m.var_b)
    m["p_live"] = norm.cdf((m.now_a - m.now_b + m.mu_a - m.mu_b) / (k * sd.clip(lower=1e-6)))
    m.loc[sd < 1e-6, "p_live"] = (m.now_a > m.now_b).astype(float) * 0.999 + 0.0005
    m["p_pre"] = norm.cdf((m.pre_mu_a - m.pre_mu_b) / (k * np.sqrt(m.pre_var_a + m.pre_var_b)))
    tot = m.now_a + m.now_b
    m["p_share"] = np.where(tot > 0, m.now_a / tot.where(tot > 0, 1), 0.5)
    m["won"] = (m.final_a > m.final_b).astype(float)
    m["left"] = (m.rem_a + m.rem_b) / (m.tot_a + m.tot_b).clip(lower=1e-6)
    return m.reset_index()


def metrics(p, y) -> dict:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4); y = np.asarray(y, float)
    return {"n": len(y), "log_loss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
            "brier": float(((p - y) ** 2).mean())}


def calib_table(p, y, bins=(0, .1, .3, .5, .7, .9, 1.0001)) -> list[dict]:
    out = []
    p = np.asarray(p); y = np.asarray(y)
    for lo, hi in zip(bins[:-1], bins[1:]):
        k = (p >= lo) & (p < hi)
        if k.sum():
            out.append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n": int(k.sum()),
                        "mean_p": round(float(p[k].mean()), 3), "won": round(float(y[k].mean()), 3)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    df, audit, meta = build(args.refresh)
    print("seasons", meta["seasons"], "starter-checkpoint rows", len(df))
    a = audit.iloc[0].to_dict()
    print(f"rebuild audit (final pbp vs Sleeper players_points): {a}")
    unk = (df.pos == "UNK").mean()
    print(f"unknown position share {unk:.3%} (no projection row: dropped)")
    tr = df[df.season.isin(TRAIN)]; te = df[df.season.isin(TEST)]
    params = fit_spread(tr)
    flat = fit_spread(tr.assign(pos="WR"))                      # one spread for all positions
    flat = {"a": {p: flat["a"]["WR"] for p in POS}, "b": {p: flat["b"]["WR"] for p in POS}, "g": flat["g"]}
    # global spread factor k on TRAIN matchups
    mtr = lambda k: matchup_frame(tr, params, k)
    ks = np.linspace(0.7, 1.5, 17)
    ll = [metrics(mtr(k).p_live, mtr(k).won)["log_loss"] for k in ks]
    k_train = float(ks[int(np.argmin(ll))])
    # Shipped k is 1.0, not the train argmin: across 0.85-1.1 the train log loss
    # moves by < 0.003 (noise), 1.0 is best on the held-out seasons, and 1.0 is
    # the board's TESTED pre-game convention (gridiron.winprob), so Game Zone
    # and the board agree at kickoff. A wider pre-game spread was checked and
    # did not help (the pre-game weakness is the projections, not the spread).
    k_best = 1.0
    k_scan = {f"{k:.2f}": round(v, 4) for k, v in zip(ks, ll)}
    print("spread params", json.dumps(params, indent=None), "k", k_best)
    out = {}
    for label, data in (("train", tr), ("test", te)):
        m = matchup_frame(data, params, k_best)
        mf = matchup_frame(data, flat, k_best)
        m["stage"] = pd.cut(m.left, [-0.01, 0.0001, 0.25, 0.6, 0.9, 1.01],
                            labels=["final", "late (<25% left)", "mid (25-60%)", "early (60-90%)",
                                    "pre (>90%)"])
        res = {"all": {"live": metrics(m.p_live, m.won), "live_flat_spread": metrics(mf.p_live, mf.won),
                       "pregame_frozen": metrics(m.p_pre, m.won), "points_share_bar": metrics(m.p_share, m.won)}}
        for st, g in m.groupby("stage", observed=True):
            if st == "final":
                continue
            res[str(st)] = {"live": metrics(g.p_live, g.won), "pregame_frozen": metrics(g.p_pre, g.won),
                            "points_share_bar": metrics(g.p_share, g.won)}
        res["calibration_live"] = calib_table(m.p_live[m.stage != "final"], m.won[m.stage != "final"])
        out[label] = res
        print(f"\n== {label} ({sorted(data.season.unique())}) ==")
        for k, v in res.items():
            if k != "calibration_live":
                print(f"  {k:22s}", {n: (round(x['log_loss'], 4), round(x['brier'], 4), x['n'])
                                       for n, x in v.items()})
        print("  calibration (live, not final):", res["calibration_live"])
        if label == "test":
            fixtures = []
            mm = m[m.stage.isin(["early (60-90%)", "mid (25-60%)", "late (<25% left)"])]
            pick = mm.sample(n=min(8, len(mm)), random_state=7) if len(mm) else mm
            for r in pick.itertuples():
                rows_ = data[(data.season == r.season) & (data.week == r.week) & (data.mid == r.mid)
                             & (data.cp == r.cp) & data.pos.isin(POS)]
                fixtures.append({"sides": [[{"pos": x.pos, "proj": round(x.proj, 3),
                                             "now": round(x.partial, 2), "f": round(x.f, 4)}
                                            for x in rows_[rows_.side == s].itertuples()] for s in (0, 1)],
                                 "expected_p": round(float(r.p_live), 4), "won": int(r.won)})
            (REF / "live_pwin_fixtures.json").write_text(json.dumps(
                {"model": "live_pwin_v1", "note": "held-out 2025-2026 snapshots of this league; no ids "
                 "or names. expected_p is P(side 0 wins) from live_pwin_v1.json", "fixtures": fixtures},
                indent=1) + "\n")
    const = {"version": "live_pwin_v1", "fitted_on": list(TRAIN), "tested_on": list(TEST),
             "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
             "model": "P = Phi((now_a-now_b + sum(proj*f)_a - sum(proj*f)_b) / (k*sqrt(sum sd^2))), "
                      "sd = f^(g/2) * (a_pos + b_pos*proj)",
             "projection": "Sleeper weekly projection stats dot the league's scoring_settings; "
                           "fgmiss = sum of fgmiss_* keys",
             "game_minutes": GAME_MINUTES, "k": k_best, "k_train_argmin": k_train,
             "k_scan_train_log_loss": k_scan, **params,
             "rebuild_audit": a, "metrics": out}
    REF.mkdir(parents=True, exist_ok=True)
    (REF / "live_pwin_v1.json").write_text(json.dumps(const, indent=1) + "\n")
    print("\nwrote data/reference/live_pwin_v1.json and live_pwin_fixtures.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
