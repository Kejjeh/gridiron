"""Grade the page's P(win) formula on THIS league's own matchups, 2023-2025.

    PYTHONPATH=src python scripts/research/pwin_league_history.py [--refresh] [--reps 500]

The page (gridiron.dashboard._matchup, gridiron.winprob) sums each side's
starters' projected means and variances (independence) and states

    P(win) = Phi((mu_a - mu_b) / sqrt(var_a + var_b)).

The candidate calibration is one spread factor k:
    P(win) = Phi((mu_a - mu_b) / (k * sqrt(var_a + var_b))).
This script projects every starter of every real regular-season matchup in
this league's three previous seasons AS OF that week, the page's way, and
asks which k this league's own results support.

How a starter is projected (the page's order of operations):
  * Sleeper id -> gsis_id through the ONE crosswalk (season2026/crosswalk.csv
    plus the page's id-keyed overlay from the Sleeper player map). Never a
    name (rule #3). Unresolved -> the page abstains on the whole matchup.
  * Position and team come from nflverse load_rosters_weekly for THAT season
    and week (teams change); the weekly box score is the fallback.
  * Skill and K: gridiron.projection.project on build_evidence(player_weeks(...),
    through_week=w-1), implied total from gridiron.weekly.schedule_index. No
    earlier game this season -> abstain -> the matchup is excluded (counted).
  * A usable projection on a team with no game that week (bye, or no team)
    -> 0 with sd 0. On that week's injury report as 'Out' -> 0 with sd 0.
  * Served-mean sensitivity: the skill mean replaced by advanced_v1 refit
    leave-one-season-out (pwin_calibration.loso_advanced) wherever the
    season table has the player-week; coverage is reported.
  * Team defenses: the advanced DEF ridge (AdvancedModel.adv['DEF']) on
    defense_features_as_of, sd = meta resid_sd DEF. A defense with no row
    (bye) is left out, as the page leaves it out.

Outcomes: Sleeper's own `points`, and a recomputation of each side from
nflverse actuals with the CURRENT scoring (gridiron.scoring via
player_weeks, DEF via defense_history; no stat line = 0), so the label is in
the projection's units.

Output: data/research/cache/tables/pwin_league_history.json (gitignored).
Raw Sleeper JSON is cached under data/research/cache/sleeper_history/.
Prints no player names, manager names or league ids.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk, is_dst_id, nflverse_team, normalize_id, sleeper_gsis_overlay
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT, RESEARCH_CACHE
from gridiron.projection import PROJECTABLE, build_evidence, project
from gridiron.usage import player_weeks
from gridiron.weekly import schedule_index

from research_common import load_script

API = "https://api.sleeper.app/v1"
HIST = RESEARCH_CACHE / "sleeper_history"
SEASON_DIR = RESEARCH_CACHE / "season2026"
OUT_JSON = RESEARCH_CACHE / "tables" / "pwin_league_history.json"
UA = {"User-Agent": "gridiron-research/0.1 (python-urllib)"}
SEASONS = (2025, 2024, 2023)
MIN_WEEK = 4
SLEEP_S = 0.4
SKILL_K = ("QB", "RB", "WR", "TE", "K")
POS_ALIAS = {"FB": "RB", "HB": "RB"}
KGRID = np.round(np.arange(0.80, 2.0 + 1e-9, 0.01), 2)
K_CHECK = (1.10, 1.15, 1.20, 1.25, 1.30)
BINS = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
        (0.70, 0.80), (0.80, 0.90), (0.90, 1.0000001))
#: Sleeper keys gridiron's scoring maps one-for-one (scripts/verify_league_settings.py).
CORE_KEYS = ("pass_yd", "pass_td", "pass_int", "rush_yd", "rush_td", "rec", "rec_yd",
             "rec_td", "fum_lost", "pass_2pt", "rush_2pt", "rec_2pt")
#: Status rank when a player has two weekly-roster rows (a mid-week move).
STATUS_RANK = {"ACT": 0, "RES": 1, "INA": 2, "DEV": 3, "EXE": 4, "TRD": 5, "TRC": 6}
GONE = {"CUT", "RET"}


# ----------------------------------------------------------------------------
# Plumbing
# ----------------------------------------------------------------------------
def ignored(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(path)],
                       capture_output=True)
    return r.returncode == 0


def fetch(url: str, dest: Path, refresh: bool):
    """GET a public Sleeper endpoint, cached raw. Never prints the URL (it
    carries a league id)."""
    if dest.exists() and not refresh:
        return json.loads(dest.read_text(encoding="utf-8"))
    time.sleep(SLEEP_S)
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310
                obj = json.loads(r.read().decode())
            dest.write_text(json.dumps(obj), encoding="utf-8")
            return obj
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Sleeper GET failed after 3 tries ({last}) for {dest.name}")


def league_history(refresh: bool) -> tuple[dict, dict[int, dict]]:
    """(current league, {season: {'league': obj, 'matchups': {week: rows}}})."""
    current = json.loads((SEASON_DIR / "sleeper_league.json").read_text(encoding="utf-8"))["league"]
    out: dict[int, dict] = {}
    lid = current.get("previous_league_id")
    hops = 0
    while lid and hops < 6 and not set(SEASONS) <= set(out):
        hops += 1
        lg = fetch(f"{API}/league/{lid}", HIST / f"league_{lid}.json", refresh)
        season = int(lg["season"])
        if season in SEASONS:
            pws = int((lg.get("settings") or {}).get("playoff_week_start") or 15)
            mus = {}
            for wk in range(1, pws):
                mus[wk] = fetch(f"{API}/league/{lid}/matchups/{wk}",
                                HIST / f"matchups_{season}_w{wk:02d}.json", refresh) or []
            out[season] = {"league": lg, "matchups": mus, "playoff_week_start": pws}
        lid = lg.get("previous_league_id")
    return current, out


def scoring_differences(old: dict, new: dict) -> list[dict]:
    diffs = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(float(a) - float(b)) > 1e-9:
            diffs.append({"key": key, "then": a, "current": b})
    return diffs


def phi(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, dtype=float) / math.sqrt(2.0)))


# ----------------------------------------------------------------------------
# nflverse, per season
# ----------------------------------------------------------------------------
def load_season(season: int) -> dict:
    import nflreadpy as nfl
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly.loc[weekly["season_type"] == "REG"].copy()
    weekly["gsis_id"] = weekly["player_id"].map(normalize_id)
    rost = nfl.load_rosters_weekly([season]).to_pandas()
    rost = rost.loc[rost["game_type"] == "REG"].copy()
    rost["gsis_id"] = rost["gsis_id"].map(normalize_id)
    inj = nfl.load_injuries([season]).to_pandas()
    inj = inj.loc[inj["game_type"] == "REG"].copy()
    inj["gsis_id"] = inj["gsis_id"].map(normalize_id)
    return {"weekly": weekly,
            "snaps": nfl.load_snap_counts([season]).to_pandas(),
            "sched": nfl.load_schedules([season]).to_pandas(),
            "team": nfl.load_team_stats([season], summary_level="week").to_pandas(),
            "rost": rost, "inj": inj}


def roster_lookup(rost: pd.DataFrame) -> tuple[dict, dict]:
    """({(gid, week): (team, position, status)}, {gid: position (season)})."""
    r = rost.loc[rost["gsis_id"] != ""].copy()
    r["_rank"] = r["status"].map(lambda s: 9 if s in GONE else STATUS_RANK.get(s, 7))
    r = r.sort_values(["gsis_id", "week", "_rank"])
    by_week = {}
    for row in r.drop_duplicates(["gsis_id", "week"]).itertuples():
        team = "" if row.status in GONE else str(row.team or "")
        by_week[(row.gsis_id, int(row.week))] = (team, str(row.position or ""), str(row.status))
    season_pos = r.sort_values("week").groupby("gsis_id")["position"].last().to_dict()
    return by_week, season_pos


def defense_tables(seasons, cw: Crosswalk, kdb, bt) -> pd.DataFrame:
    """DEF feature rows, weeks >= MIN_WEEK, the k_def_backtest way (incl. its
    Sleeper-projection filter), for refitting the ridge out of season."""
    import nflreadpy as nfl
    frames = []
    for s in seasons:
        sched = nfl.load_schedules([s]).to_pandas()
        team = nfl.load_team_stats([s], summary_level="week").to_pandas()
        dh = A.defense_history(team, sched)
        weeks = sorted(int(w) for w in dh["week"].unique() if int(w) >= 2)
        rows = [A.defense_features_as_of(dh, w, sched).assign(week=w) for w in weeks]
        D = pd.concat(rows).merge(dh[["team", "week", "dst_points"]], on=["team", "week"])
        D = D.rename(columns={"dst_points": "actual"})
        sl = kdb.sleeper(bt, s, weeks, "DEF", cw).rename(columns={"key": "team"})
        D = D.merge(sl, on=["week", "team"], how="left").assign(season=s)
        frames.append(D.loc[(D["week"] >= MIN_WEEK) & D["sleeper"].notna() & D["actual"].notna()])
    return pd.concat(frames, ignore_index=True)


# ----------------------------------------------------------------------------
# Starters, projected as of their week
# ----------------------------------------------------------------------------
def season_starters(season: int, hist: dict, cw: Crosswalk, model, def_oos) -> pd.DataFrame:
    d = load_season(season)
    weekly, sched = d["weekly"], d["sched"]
    pw = player_weeks(weekly.loc[weekly["position"].isin(SKILL_K)], d["snaps"], cw)
    pw["gsis_id"] = pw["gsis_id"].astype(str)
    dh = A.defense_history(d["team"], sched)
    dst = {(str(r.team), int(r.week)): r.dst_points for r in dh.itertuples()}
    by_week, season_pos = roster_lookup(d["rost"])
    box = {(r.gsis_id, int(r.week)): (str(r.team or ""), str(r.position or ""))
           for r in weekly.itertuples()}
    def_sd = float((model.meta.get("resid_sd") or {}).get("DEF") or 0.0)

    weeks = sorted(w for w in hist["matchups"] if w >= MIN_WEEK)
    # actual league points (CURRENT scoring) for every starter with a stat line
    gids = set()
    for w in weeks:
        for row in hist["matchups"][w]:
            for sid in row.get("starters") or []:
                sid = normalize_id(sid)
                if sid and sid != "0" and not is_dst_id(sid) and cw.gsis(sid):
                    gids.add(cw.gsis(sid))
    lines = weekly.loc[weekly["gsis_id"].isin(gids)]
    act = player_weeks(lines, None, cw)
    actual = act.groupby(["gsis_id", "week"])["league_points"].sum().to_dict()

    out = []
    for w in weeks:
        rows = [r for r in hist["matchups"][w] if r.get("matchup_id") is not None]
        if not rows:
            continue
        ev = build_evidence(pw, through_week=w - 1)
        games = schedule_index(sched, w)
        outs = set(d["inj"].loc[(d["inj"]["week"] == w)
                                & (d["inj"]["report_status"].astype(str) == "Out"), "gsis_id"])
        drows = {r["team"]: r for r in A.defense_features_as_of(dh, w, sched).to_dict("records")}
        for row in rows:
            starters = list(row.get("starters") or [])
            sl_pts = list(row.get("starters_points") or [None] * len(starters))
            for slot, raw in enumerate(starters):
                sid = normalize_id(raw)
                rec = {"season": season, "week": w, "matchup_id": int(row["matchup_id"]),
                       "roster_id": int(row["roster_id"]), "slot": slot,
                       "sleeper_team_points": float(row.get("points") or 0.0),
                       "sleeper_starter_points": (float(sl_pts[slot]) if slot < len(sl_pts)
                                                  and sl_pts[slot] is not None else np.nan),
                       "kind": "player", "pos": "", "abstain": "", "mean": np.nan,
                       "sd": np.nan, "bye": False, "out": False, "res": False,
                       "def_mean": np.nan, "def_mean_oos": np.nan, "actual": 0.0,
                       "gsis_id": ""}
                if not sid or sid == "0":
                    rec["kind"] = "empty"
                elif is_dst_id(sid):
                    team = nflverse_team(sid)
                    rec.update(kind="def", pos="DEF", sd=def_sd)
                    feat = drows.get(team)
                    if feat is not None:
                        rec["def_mean"] = model.adv["DEF"].predict_row(feat)
                        if def_oos is not None:
                            rec["def_mean_oos"] = def_oos[season].predict_row(feat)
                    v = dst.get((team, w))
                    rec["actual"] = float(v) if v is not None and v == v else 0.0
                else:
                    gid = cw.gsis(sid)
                    if not gid:
                        rec["abstain"] = "unresolved_id"
                    else:
                        team, pos, status = by_week.get((gid, w), ("", "", ""))
                        if (gid, w) not in by_week and (gid, w) in box:
                            team, pos = box[(gid, w)]
                        pos = pos or season_pos.get(gid) or \
                            (ev.players[gid].position if gid in ev.players else "")
                        pos = POS_ALIAS.get(pos.upper(), pos.upper())
                        rec.update(gsis_id=gid, pos=pos, res=(status == "RES"), out=(gid in outs),
                                   bye=(team not in games),
                                   actual=float(actual.get((gid, w), 0.0)))
                        if pos not in PROJECTABLE:
                            rec["abstain"] = "position_not_projectable"
                        else:
                            game = games.get(team)
                            p = project(ev.players.get(gid), position=pos, week=w,
                                        implied_total=game.implied_total if game else None,
                                        evidence=ev)
                            if not p.usable:
                                rec["abstain"] = "no_earlier_game"
                            else:
                                rec["mean"], rec["sd"] = float(p.mean), float(p.sd)
                out.append(rec)
    print(f"  {season}: projected starters for weeks {weeks[0]}-{weeks[-1]}", flush=True)
    return pd.DataFrame(out)


def starter_values(S: pd.DataFrame, *, res: bool = False, rescue: bool = False,
                   def_oos: bool = False, served: bool = False) -> pd.DataFrame:
    """mean / var / blocker per starter under one variant of the page rules.
    `served`: the skill mean is advanced_v1 out of sample where the season
    table has the row (`served_means`), the baseline otherwise."""
    S = S.copy()
    mean = np.zeros(len(S))
    var = np.zeros(len(S))
    block = np.zeros(len(S), dtype=bool)
    k = S["kind"].to_numpy()
    withheld = (S["bye"] | S["out"] | (S["res"] if res else False)).to_numpy()
    abst = S["abstain"].to_numpy()
    dm = (S["def_mean_oos"] if def_oos else S["def_mean"]).to_numpy(dtype=float)
    m = (S["mean_served"] if served else S["mean"]).to_numpy(dtype=float)
    sd = S["sd"].to_numpy(dtype=float)
    for i in range(len(S)):
        if k[i] == "empty":
            continue
        if k[i] == "def":
            if dm[i] == dm[i]:
                mean[i], var[i] = dm[i], sd[i] ** 2
            continue                          # an unprojected DEF is left out
        if abst[i]:
            if rescue and abst[i] == "no_earlier_game" and withheld[i]:
                continue                      # withheld before abstaining
            block[i] = True
            continue
        if withheld[i]:
            continue
        mean[i], var[i] = m[i], sd[i] ** 2
    S["v_mean"], S["v_var"], S["v_block"] = mean, var, block
    return S


def matchups(S: pd.DataFrame, **variant) -> tuple[pd.DataFrame, Counter]:
    """One row per real matchup (A = lower roster id), excluded ones counted."""
    V = starter_values(S, **variant)
    side = V.groupby(["season", "week", "matchup_id", "roster_id"]).agg(
        mu=("v_mean", "sum"), var=("v_var", "sum"), block=("v_block", "any"),
        sleeper=("sleeper_team_points", "first"), recomputed=("actual", "sum"),
        n_proj=("kind", lambda x: int((x != "empty").sum()))).reset_index()
    reasons = (V.loc[V["v_block"]].groupby(["season", "week", "matchup_id"])["abstain"]
               .agg(lambda x: tuple(sorted(set(x)))))
    rows, excluded = [], Counter()
    for (s, w, mid), g in side.groupby(["season", "week", "matchup_id"]):
        if len(g) != 2:
            excluded["not_two_sides"] += 1
            continue
        a, b = g.sort_values("roster_id").to_dict("records")
        if a["block"] or b["block"]:
            for r in reasons.get((s, w, mid), ("?",)):
                excluded[r] += 1
            excluded["_matchups"] += 1
            continue
        if a["n_proj"] == 0 or b["n_proj"] == 0:
            excluded["empty_lineup"] += 1
            excluded["_matchups"] += 1
            continue
        rows.append({"season": s, "week": w, "matchup_id": mid,
                     "d": a["mu"] - b["mu"], "s": math.sqrt(a["var"] + b["var"]),
                     "mu_a": a["mu"], "mu_b": b["mu"], "var_a": a["var"], "var_b": b["var"],
                     "a_sleeper": a["sleeper"], "b_sleeper": b["sleeper"],
                     "a_recomputed": a["recomputed"], "b_recomputed": b["recomputed"],
                     "m_sleeper": a["sleeper"] - b["sleeper"],
                     "m_recomputed": a["recomputed"] - b["recomputed"]})
    M = pd.DataFrame(rows)
    for lab in ("sleeper", "recomputed"):
        m = M[f"m_{lab}"]
        M[f"y_{lab}"] = np.where(m > 1e-9, 1.0, np.where(m < -1e-9, 0.0, 0.5))
    return M, excluded


def served_means(S: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Add `mean_served`: advanced_v1 refit leave-one-season-out (the page's
    mean without Sleeper's number, `pwin_calibration.loso_advanced`), joined
    on (season, week, gsis_id) from the season tables. Those tables hold
    only players who PLAYED, so a starter without a row keeps the baseline:
    coverage is reported, and the baseline columns are compared on the
    joined rows to prove the join (same projection, same id, same week)."""
    cal = load_script("pwin_calibration", "scripts/research/pwin_calibration.py")
    loso = cal.loso_advanced(range(2019, 2026))
    adv, base = {}, {}
    for season in sorted(S["season"].unique()):
        t = pd.read_parquet(cal.TABLES / f"season{int(season)}_shipped.parquet",
                            columns=["week", "gsis_id", "position", "baseline"])
        t["adv"] = loso[int(season)]
        for r in t.itertuples():
            key = (int(season), int(r.week), str(r.gsis_id))
            adv[key], base[key] = float(r.adv), float(r.baseline)
    S = S.copy()
    keys = list(zip(S["season"].astype(int), S["week"].astype(int), S["gsis_id"].astype(str)))
    a = np.array([adv.get(k, np.nan) for k in keys])
    b = np.array([base.get(k, np.nan) for k in keys])
    elig = ((S["kind"] == "player") & S["abstain"].eq("") & S["pos"].isin(A.POSITIONS)
            & S["mean"].notna()).to_numpy()
    use = elig & np.isfinite(a)
    S["mean_served"] = np.where(use, a, S["mean"].to_numpy(dtype=float))
    gap = np.abs(b[use] - S["mean"].to_numpy(dtype=float)[use])
    info = {"skill_starter_slots_projected": int(elig.sum()), "with_advanced_mean": int(use.sum()),
            "baseline_join_check_max_abs_diff": round(float(gap.max()), 4) if use.any() else None,
            "baseline_join_check_median_abs_diff": round(float(np.median(gap)), 4) if use.any() else None,
            "mean_shift_served_minus_baseline": round(float(np.mean(a[use] - S["mean"].to_numpy(
                dtype=float)[use])), 3) if use.any() else None,
            "note": "a starter with no season-table row (did not play) keeps the baseline mean"}
    print(f"served means: {info}")
    return S, info


# ----------------------------------------------------------------------------
# Scoring the formula
# ----------------------------------------------------------------------------
def per_row_losses(d, s, y, ks):
    z = d[None, :] / (np.asarray(ks)[:, None] * s[None, :])
    p = np.clip(phi(z), 1e-12, 1 - 1e-12)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    br = (p - y) ** 2
    return p, ll, br


def reliability(p: np.ndarray, y: np.ndarray) -> list[dict]:
    fav = np.where(p >= 0.5, p, 1 - p)
    won = np.where(p >= 0.5, y, 1 - y)
    out = []
    for lo, hi in BINS:
        sel = (fav >= lo) & (fav < hi)
        n = int(sel.sum())
        out.append({"bin": f"{int(lo * 100)}-{int(round(min(hi, 1.0) * 100))}", "n": n,
                    "stated": round(float(fav[sel].mean()), 4) if n else None,
                    "observed": round(float(won[sel].mean()), 4) if n else None})
    return out


def zstats(M: pd.DataFrame, lab: str) -> dict:
    z = (M[f"m_{lab}"] - M["d"]) / M["s"]
    mad = float(np.median(np.abs(z - np.median(z)))) * 1.4826
    slope, icpt = np.polyfit(M["d"], M[f"m_{lab}"], 1)
    sign = np.where(M["d"] >= 0, 1.0, -1.0)
    pts = np.concatenate([M[f"a_{lab}"], M[f"b_{lab}"]])
    mu = np.concatenate([M["mu_a"], M["mu_b"]])
    sd = np.sqrt(np.concatenate([M["var_a"], M["var_b"]]))
    zt = (pts - mu) / sd
    return {"zsd": round(float(z.std(ddof=1)), 4),
            "z_mean_side_a": round(float(z.mean()), 4),
            "z_mean_favourite": round(float((sign * z).mean()), 4),
            "z_robust_sd": round(mad, 4),
            "margin_slope_on_projected": round(float(slope), 4),
            "margin_intercept": round(float(icpt), 3),
            "corr_projected_actual_margin": round(float(np.corrcoef(M["d"], M[f"m_{lab}"])[0, 1]), 4),
            "projected_margin_sd": round(float(M["d"].std(ddof=1)), 3),
            "actual_margin_sd": round(float(M[f"m_{lab}"].std(ddof=1)), 3),
            "mean_formula_sd": round(float(M["s"].mean()), 3),
            "rmse_margin": round(float(np.sqrt(((M[f"m_{lab}"] - M["d"]) ** 2).mean())), 3),
            "team": {"n": int(len(pts)), "mean_projected": round(float(mu.mean()), 2),
                     "mean_actual": round(float(pts.mean()), 2),
                     "bias_actual_minus_projected": round(float((pts - mu).mean()), 2),
                     "zsd": round(float(zt.std(ddof=1)), 4),
                     "mean_sd": round(float(sd.mean()), 2),
                     "corr": round(float(np.corrcoef(mu, pts)[0, 1]), 4)}}


def grade(M: pd.DataFrame, lab: str, reps: int, seed: int) -> dict:
    d, s, y = M["d"].to_numpy(), M["s"].to_numpy(), M[f"y_{lab}"].to_numpy()
    P, LL, BR = per_row_losses(d, s, y, KGRID)
    ll_k, br_k = LL.mean(axis=1), BR.mean(axis=1)
    i1 = int(np.argmin(np.abs(KGRID - 1.0)))
    im = int(np.argmin(ll_k))
    k_mle = float(KGRID[im])
    res = {"n": int(len(M)), "ties": int((y == 0.5).sum()),
           "raw": {"logloss": round(float(ll_k[i1]), 5), "brier": round(float(br_k[i1]), 5),
                   **zstats(M, lab)},
           "k_mle": k_mle, "k_mle_on_boundary": bool(im in (0, len(KGRID) - 1)),
           "at_k_mle": {"logloss": round(float(ll_k[im]), 5), "brier": round(float(br_k[im]), 5)},
           "k_brier_min": float(KGRID[int(np.argmin(br_k))]),
           "k_profile_95_naive": [float(KGRID[(len(M) * (ll_k - ll_k[im])) <= 1.92].min()),
                                  float(KGRID[(len(M) * (ll_k - ll_k[im])) <= 1.92].max())],
           "by_k": {f"{k:.2f}": {"logloss": round(float(ll_k[int(np.argmin(np.abs(KGRID - k)))]), 5),
                                 "brier": round(float(br_k[int(np.argmin(np.abs(KGRID - k)))]), 5)}
                    for k in (1.0, *K_CHECK, k_mle)},
           "reliability_raw": reliability(P[i1], y),
           "reliability_at_k_mle": reliability(P[im], y)}
    # season detail: the favourite's side
    by_season = {}
    for season in sorted(M["season"].unique()):
        sel = (M["season"] == season).to_numpy()
        row = {"n": int(sel.sum())}
        for tag, idx in (("raw", i1), ("k_mle", im)):
            p = P[idx][sel]
            fav = np.where(p >= 0.5, p, 1 - p)
            won = np.where(p >= 0.5, y[sel], 1 - y[sel])
            row[tag] = {"fav_stated": round(float(fav.mean()), 4),
                        "fav_observed": round(float(won.mean()), 4),
                        "logloss": round(float(LL[idx][sel].mean()), 5),
                        "brier": round(float(BR[idx][sel].mean()), 5)}
        by_season[str(int(season))] = row
    res["by_season"] = by_season
    # leave one season out: fit k on the other two, score the third
    loso = {}
    for season in sorted(M["season"].unique()):
        tr = (M["season"] != season).to_numpy()
        kk = int(np.argmin(LL[:, tr].mean(axis=1)))
        te = ~tr
        loso[str(int(season))] = {"k_fit_on_others": float(KGRID[kk]), "n_test": int(te.sum()),
                                  "logloss_raw": round(float(LL[i1, te].mean()), 5),
                                  "logloss_k": round(float(LL[kk, te].mean()), 5),
                                  "brier_raw": round(float(BR[i1, te].mean()), 5),
                                  "brier_k": round(float(BR[kk, te].mean()), 5)}
    n_te = sum(v["n_test"] for v in loso.values())
    res["loso"] = {"by_season": loso, "pooled": {
        k: round(sum(v[k] * v["n_test"] for v in loso.values()) / n_te, 5)
        for k in ("logloss_raw", "logloss_k", "brier_raw", "brier_k")}}
    # bootstrap clustered by week: resample (season, week) clusters
    cl = pd.factorize(M["season"].astype(str) + "-" + M["week"].astype(str))[0]
    C = int(cl.max()) + 1
    rng = np.random.default_rng(seed)
    ks, dll_f, dbr_f, dll_r, dbr_r = [], [], [], [], []
    chk = {k: ([], []) for k in K_CHECK}
    for _ in range(reps):
        cnt = np.bincount(rng.integers(0, C, C), minlength=C)[cl].astype(float)
        llb, brb = LL @ cnt / cnt.sum(), BR @ cnt / cnt.sum()
        j = int(np.argmin(llb))
        ks.append(KGRID[j])
        dll_f.append(llb[i1] - llb[im])
        dbr_f.append(brb[i1] - brb[im])
        dll_r.append(llb[i1] - llb[j])
        dbr_r.append(brb[i1] - brb[j])
        for k in K_CHECK:
            ik = int(np.argmin(np.abs(KGRID - k)))
            chk[k][0].append(llb[i1] - llb[ik])
            chk[k][1].append(brb[i1] - brb[ik])
    q = lambda a: [round(float(np.percentile(a, 2.5)), 5), round(float(np.percentile(a, 97.5)), 5)]  # noqa: E731
    res["bootstrap"] = {
        "reps": reps, "seed": seed, "clusters": C, "cluster": "season-week",
        "k_ci": q(ks), "k_median": float(np.median(ks)),
        "share_k_gt_1": round(float(np.mean(np.array(ks) > 1.0)), 4),
        "share_at_grid_low_0.80": round(float(np.mean(np.array(ks) <= KGRID[0] + 1e-9)), 4),
        "share_at_grid_high_2.00": round(float(np.mean(np.array(ks) >= KGRID[-1] - 1e-9)), 4),
        "logloss_improvement_at_k_mle": {"point": round(float(ll_k[i1] - ll_k[im]), 5), "ci": q(dll_f)},
        "brier_improvement_at_k_mle": {"point": round(float(br_k[i1] - br_k[im]), 5), "ci": q(dbr_f)},
        "logloss_improvement_refit": {"ci": q(dll_r), "note": "k refit per replicate; >= 0 by construction"},
        "brier_improvement_refit": {"ci": q(dbr_r)},
        "improvement_at_k": {f"{k:.2f}": {
            "logloss": {"point": round(float(ll_k[i1] - ll_k[int(np.argmin(np.abs(KGRID - k)))]), 5),
                        "ci": q(chk[k][0])},
            "brier": {"point": round(float(br_k[i1] - br_k[int(np.argmin(np.abs(KGRID - k)))]), 5),
                      "ci": q(chk[k][1])}} for k in K_CHECK}}
    res["k_ci"] = res["bootstrap"]["k_ci"]
    return res


# ----------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="re-GET the Sleeper JSON")
    ap.add_argument("--reps", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--no-def-oos", action="store_true",
                    help="skip refitting the DEF ridge out of season (sensitivity)")
    args = ap.parse_args(argv)

    for target in (HIST / "x.json", OUT_JSON):
        if not ignored(target):
            print(f"refusing: {target.relative_to(REPO_ROOT)} is not gitignored", file=sys.stderr)
            return 2
    HIST.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)

    current, hist = league_history(args.refresh)
    missing = [s for s in SEASONS if s not in hist]
    if missing:
        print(f"league chain does not reach season(s) {missing}", file=sys.stderr)
    seasons = sorted(hist)
    print(f"seasons in the chain: {seasons}")

    # ---- settings that changed between those seasons and now
    cur_sc = current.get("scoring_settings") or {}
    sdiff = {str(s): scoring_differences(hist[s]["league"].get("scoring_settings") or {}, cur_sc)
             for s in seasons}
    rpos = {str(s): hist[s]["league"].get("roster_positions") for s in seasons}
    rpos_same = {s: v == current.get("roster_positions") for s, v in rpos.items()}
    for s in seasons:
        lg = hist[s]["league"]
        print(f"{s}: teams {lg.get('total_rosters')}, playoff_week_start "
              f"{hist[s]['playoff_week_start']}, roster positions same as now: {rpos_same[str(s)]}")
        for dd in sdiff[str(s)]:
            print(f"    scoring differs: {dd['key']}: {dd['then']} -> {dd['current']}")
        if not sdiff[str(s)]:
            print("    scoring identical to the current season")

    # ---- crosswalk: the page's (CSV + id-keyed overlay from the Sleeper map)
    cw = Crosswalk.from_csv(SEASON_DIR / "crosswalk.csv")
    players = json.loads((SEASON_DIR / "sleeper_players.json").read_text(encoding="utf-8"))
    if isinstance(players, dict) and "players" in players and isinstance(players["players"], dict):
        players = players["players"]
    cw_page = cw.with_overlay(sleeper_gsis_overlay(players))
    model = A.AdvancedModel.load()

    def_oos, def_check = None, {}
    if not args.no_def_oos:
        kdb = load_script("k_def_backtest", "scripts/research/k_def_backtest.py")
        bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
        D = defense_tables(range(2019, 2026), cw, kdb, bt)
        cols = list(A.DEFENSE_FEATURES)
        full = A.Ridge.fit(D, D["actual"], cols)
        shipped = model.adv["DEF"]
        def_check = {"refit_2019_2025_max_abs_beta_diff":
                     round(float(np.max(np.abs(np.array(full.beta) - np.array(shipped.beta)))), 6),
                     "shipped_beta_l1": round(float(np.sum(np.abs(shipped.beta))), 4)}
        for lo_hi in ((2019, 2024),):
            sub = D.loc[D["season"].between(*lo_hi)]
            r = A.Ridge.fit(sub, sub["actual"], cols)
            def_check["refit_2019_2024_max_abs_beta_diff"] = round(
                float(np.max(np.abs(np.array(r.beta) - np.array(shipped.beta)))), 6)
        def_oos = {s: A.Ridge.fit(D.loc[D["season"] != s], D.loc[D["season"] != s, "actual"], cols)
                   for s in seasons}
        print(f"DEF ridge training check: {def_check}")

    S = pd.concat([season_starters(s, hist[s], cw_page, model, def_oos) for s in seasons],
                  ignore_index=True)
    sids_all = []
    for s in seasons:
        for w, rows in hist[s]["matchups"].items():
            if w < MIN_WEEK:
                continue
            for row in rows:
                if row.get("matchup_id") is None:
                    continue
                sids_all += [normalize_id(x) for x in row.get("starters") or []
                             if normalize_id(x) not in ("", "0") and not is_dst_id(x)]
    n_csv = sum(1 for x in sids_all if cw.gsis(x))
    n_page = sum(1 for x in sids_all if cw_page.gsis(x))
    print(f"starter-slots (players): {len(sids_all)}; resolved by CSV {n_csv}, "
          f"+overlay {n_page - n_csv}, unresolved {len(sids_all) - n_page}")

    # ---- outcomes: Sleeper's own points vs the recomputation
    M0, ex0 = matchups(S)
    agree = float(((M0["y_sleeper"] == M0["y_recomputed"])).mean())
    team_gap = []
    for (s, w, mid, rid), g in S.groupby(["season", "week", "matchup_id", "roster_id"]):
        team_gap.append(g["sleeper_team_points"].iloc[0] - g["actual"].sum())
    team_gap = np.array(team_gap)
    pos_gap = (S.assign(gap=S["sleeper_starter_points"] - S["actual"])
                .loc[S["kind"] != "empty"]
                .groupby(S["pos"].where(S["kind"] == "player", "DEF"))["gap"]
                .agg(lambda x: round(float(np.nanmean(np.abs(x))), 3)).to_dict())
    any_core = any(dd["key"] in CORE_KEYS for v in sdiff.values() for dd in v)
    any_diff = any(sdiff.values())
    material = bool(any_core or np.mean(np.abs(team_gap)) > 2.0)
    primary = "recomputed" if (any_diff and material) else "sleeper"
    print(f"\nwinner agreement Sleeper vs recomputed: {agree:.1%} of {len(M0)} matchups; "
          f"team-week |Sleeper - recomputed| mean {np.mean(np.abs(team_gap)):.2f}, "
          f"median {np.median(np.abs(team_gap)):.2f}, max {np.max(np.abs(team_gap)):.1f}")
    print(f"per-starter mean |gap| by position: {pos_gap}")
    print(f"primary outcome label: {primary} (scoring differs: {any_diff}; core key differs: "
          f"{any_core}; material: {material})")

    # ---- starter accounting
    P = S.loc[S["kind"] == "player"]
    acct = {"starter_slots": int(len(S)), "empty_slots": int((S["kind"] == "empty").sum()),
            "def_slots": int((S["kind"] == "def").sum()),
            "def_unprojected": int(((S["kind"] == "def") & S["def_mean"].isna()).sum()),
            "player_slots": int(len(P)),
            "abstain": {k: int(v) for k, v in P["abstain"].replace("", np.nan).dropna()
                        .value_counts().items()},
            "projected_bye_or_no_team_zero": int((P["abstain"].eq("") & P["bye"]).sum()),
            "projected_out_zero": int((P["abstain"].eq("") & ~P["bye"] & P["out"]).sum()),
            "projected_reserve_list_not_withheld": int((P["abstain"].eq("") & ~P["bye"]
                                                        & ~P["out"] & P["res"]).sum()),
            "no_earlier_game_but_bye_or_out": int((P["abstain"].eq("no_earlier_game")
                                                   & (P["bye"] | P["out"])).sum()),
            "withheld_zero_but_scored": int((P["abstain"].eq("") & (P["bye"] | P["out"])
                                             & (P["actual"].abs() > 0)).sum()),
            "def_unprojected_but_scored": int(((S["kind"] == "def") & S["def_mean"].isna()
                                               & (S["actual"].abs() > 0)).sum())}
    print(f"starter accounting: {acct}")

    # ---- grade: primary, and sensitivities
    res = grade(M0, primary, args.reps, args.seed)
    other = "sleeper" if primary == "recomputed" else "recomputed"
    sens = {f"label_{other}": grade(M0, other, args.reps, args.seed)}
    S, served_info = served_means(S)
    for name, var in (("served_mean_advanced_out_of_sample", {"served": True}),
                      ("withhold_reserve_list_too", {"res": True}),
                      ("withhold_before_abstain", {"rescue": True}),
                      ("def_ridge_out_of_season", {"def_oos": True})):
        if name == "def_ridge_out_of_season" and def_oos is None:
            continue
        Mv, exv = matchups(S, **var)
        g = grade(Mv, primary, args.reps, args.seed)
        g["n_excluded"] = int(exv.get("_matchups", 0))
        sens[name] = g

    def brief(g):
        return {k: g[k] for k in ("n", "k_mle", "k_ci")} | {
            "logloss_raw": g["raw"]["logloss"], "logloss_k_mle": g["at_k_mle"]["logloss"],
            "brier_raw": g["raw"]["brier"], "brier_k_mle": g["at_k_mle"]["brier"],
            "zsd": g["raw"]["zsd"], **({"n_excluded": g["n_excluded"]} if "n_excluded" in g else {})}

    total_matchups = int(sum(len({r["matchup_id"] for r in hist[s]["matchups"][w]
                                  if r.get("matchup_id") is not None})
                             for s in seasons for w in hist[s]["matchups"] if w >= MIN_WEEK))
    excl = {k: int(v) for k, v in ex0.items() if k != "_matchups"}
    out = {
        "generated_by": "scripts/research/pwin_league_history.py",
        "seasons": seasons, "min_week": MIN_WEEK,
        "weeks": {str(s): [MIN_WEEK, hist[s]["playoff_week_start"] - 1] for s in seasons},
        "n_matchups_total": total_matchups,
        "n_matchups": res["n"],
        "n_excluded": int(ex0.get("_matchups", 0)),
        "excluded_why": {"blocking_starter_reasons (a matchup can count under several)": excl,
                         "rule": "the page abstains on a matchup with any unprojected non-DEF "
                                 "starter; exclusions are those matchups"},
        "starter_accounting": acct,
        "crosswalk": {"player_starter_slots": len(sids_all), "resolved_csv": n_csv,
                      "resolved_overlay_only": n_page - n_csv,
                      "unresolved": len(sids_all) - n_page},
        "scoring_differences": sdiff,
        "roster_positions_same_as_current": rpos_same,
        "outcome_label": primary,
        "outcome_label_reason": (
            "scoring_settings differ from the current season and the difference is material "
            "(core stat weight or a >2-pt mean team-week gap), so the label is each side's total "
            "recomputed from nflverse actuals with the current scoring — the projection's units"
            if primary == "recomputed" else
            "scoring matches (or differs immaterially); Sleeper's own points are the label"),
        "agreement_rate": round(agree, 4),
        "team_week_gap_sleeper_minus_recomputed": {
            "mean_abs": round(float(np.mean(np.abs(team_gap))), 3),
            "median_abs": round(float(np.median(np.abs(team_gap))), 3),
            "mean": round(float(np.mean(team_gap)), 3),
            "max_abs": round(float(np.max(np.abs(team_gap))), 2)},
        "starter_gap_mean_abs_by_position": pos_gap,
        "def_model": {"sd": float((model.meta.get("resid_sd") or {}).get("DEF") or 0.0),
                      "k_def_evidence_train": (model.meta.get("k_def_evidence") or {}).get("train"),
                      "k_def_evidence_test": (model.meta.get("k_def_evidence") or {}).get("test"),
                      "training_check": def_check,
                      "note": "the shipped DEF ridge's training seasons overlap the graded "
                              "seasons (see training_check); def_ridge_out_of_season refits it "
                              "leaving each graded season out"},
        "skill_mean_note": "skill/K mean = gridiron.projection baseline (the page's sd); the "
                           "page's served mean is the advanced/stack refinement of it, graded "
                           "in sensitivity.served_mean_advanced_out_of_sample",
        "served_mean": served_info,
        "raw": {"logloss": res["raw"]["logloss"], "brier": res["raw"]["brier"],
                "zsd": res["raw"]["zsd"]},
        "margin": {k: v for k, v in res["raw"].items() if k not in ("logloss", "brier")},
        "by_k": res["by_k"],
        "k_mle": res["k_mle"], "k_mle_on_boundary": res["k_mle_on_boundary"],
        "k_ci": res["k_ci"], "k_profile_95_naive": res["k_profile_95_naive"],
        "at_k_mle": res["at_k_mle"], "k_brier_min": res["k_brier_min"],
        "bootstrap": res["bootstrap"],
        "loso": res["loso"],
        "by_season": res["by_season"],
        "reliability_raw": res["reliability_raw"],
        "reliability_at_k_mle": res["reliability_at_k_mle"],
        "ties": res["ties"],
        "sensitivity": {k: brief(v) for k, v in sens.items()},
        "sensitivity_detail": sens,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")

    # ---- print
    print(f"\n=== P(win) on this league's matchups {seasons}, weeks >= {MIN_WEEK} ===")
    print(f"matchups {total_matchups}: graded {res['n']}, excluded {out['n_excluded']} {excl}")
    print(f"label: {primary}; agreement with Sleeper's winner {agree:.1%}; ties {res['ties']}")
    r = res["raw"]
    print(f"raw  (k=1): logloss {r['logloss']:.4f}  Brier {r['brier']:.4f}  z-SD {r['zsd']:.3f} "
          f"(robust {r['z_robust_sd']:.3f}, favourite mean {r['z_mean_favourite']:+.3f})")
    print(f"     team totals: {r['team']}")
    print(f"     margin: slope {r['margin_slope_on_projected']:.3f}, corr "
          f"{r['corr_projected_actual_margin']:.3f}, mean formula sd {r['mean_formula_sd']:.1f}, "
          f"RMSE {r['rmse_margin']:.1f}")
    b = res["bootstrap"]
    print(f"k_mle {res['k_mle']:.2f}  CI {b['k_ci']} (reps at 0.80: {b['share_at_grid_low_0.80']:.1%}, "
          f"at 2.00: {b['share_at_grid_high_2.00']:.1%}; naive profile-LL 95% "
          f"{res['k_profile_95_naive']})  (Brier-min k {res['k_brier_min']:.2f})  "
          f"logloss {res['at_k_mle']['logloss']:.4f}  Brier {res['at_k_mle']['brier']:.4f}")
    print(f"  improvement at k_mle: logloss {b['logloss_improvement_at_k_mle']}  "
          f"Brier {b['brier_improvement_at_k_mle']}")
    print("  k     logloss  Brier   dLL CI              dBrier CI")
    for k in (1.0, *K_CHECK):
        bk = res["by_k"][f"{k:.2f}"]
        ci = b["improvement_at_k"].get(f"{k:.2f}")
        tail = f"  {ci['logloss']['ci']}  {ci['brier']['ci']}" if ci else ""
        print(f"  {k:.2f}  {bk['logloss']:.4f}  {bk['brier']:.4f}{tail}")
    print(f"LOSO (k fit on the other seasons): {res['loso']}")
    print("reliability (favourite)  raw | at k_mle")
    for a, c in zip(res["reliability_raw"], res["reliability_at_k_mle"]):
        print(f"  {a['bin']:>6}: n {a['n']:3d} stated {a['stated']} observed {a['observed']} | "
              f"n {c['n']:3d} stated {c['stated']} observed {c['observed']}")
    print(f"by season: {res['by_season']}")
    print("sensitivity:")
    for k, v in out["sensitivity"].items():
        print(f"  {k}: {v}")
    print(f"\nwrote {OUT_JSON.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
