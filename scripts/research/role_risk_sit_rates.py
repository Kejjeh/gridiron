"""How often does an RB starter on the role-at-risk watch actually sit?

Research only: never imported by src/gridiron, writes nothing. The false-alarm
rate behind the board's RB role-at-risk rows (`gridiron.openings.role_risks`,
Kejjeh/gridiron#29, owner decision 2026-11-18 "DNP or Questionable").

The subset: an RB whose final weekly injury report (nflverse `load_injuries`,
regular season) says Questionable, on 45%+ of his team's offensive snaps over
his previous 3 games (at least 2 with snaps, all within the last 5 weeks).
"Sat" = no snap-count row with snaps that week. Split by the report's practice
status: did not participate (the board's DNP row) or practised in some form.

Caveat: Sleeper's own Questionable tag is the trigger on the board, and its
history is not kept, so nflverse's final-report Questionable stands in for it.
The joins are by id: gsis -> pfr through the cached crosswalk (rule #3).

    PYTHONPATH=src python scripts/research/role_risk_sit_rates.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from gridiron.ids import Crosswalk, normalize_id
from gridiron.paths import RESEARCH_CACHE

SNAP_BAR = 45.0
GAMES, MIN_GAMES, WINDOW = 3, 2, 5


def season_rows(season: int, cw: Crosswalk) -> list[tuple]:
    import nflreadpy as nfl
    inj = nfl.load_injuries([season]).to_pandas()
    inj = inj.loc[(inj.get("game_type", "REG") == "REG") & (inj["position"] == "RB")]
    snaps = nfl.load_snap_counts([season]).to_pandas()
    if "game_type" in snaps:
        snaps = snaps.loc[snaps["game_type"] == "REG"]
    sn = snaps.assign(pfr=snaps["pfr_player_id"].map(normalize_id),
                      pct=pd.to_numeric(snaps["offense_pct"], errors="coerce"))
    sn = sn.loc[sn["pct"].notna()]
    last_week = int(sn["week"].max())
    by = {k: g.sort_values("week") for k, g in sn.groupby("pfr")}
    out = []
    for _, r in inj.iterrows():
        if str(r.get("report_status") or "") != "Questionable":
            continue
        wk = int(r["week"])
        if wk > last_week:
            continue                                  # not played yet
        g = by.get(cw.pfr(normalize_id(r["gsis_id"])) or "")
        if g is None:
            continue                                  # no crosswalk edge: not counted
        prior = g.loc[g["week"] < wk].tail(GAMES)
        if len(prior) < MIN_GAMES or prior["week"].min() < wk - WINDOW:
            continue
        avg = prior["pct"].mean()
        avg = avg * 100 if avg <= 1.0 else avg        # nflverse stores a fraction
        if avg < SNAP_BAR:
            continue
        this = g.loc[g["week"] == wk, "pct"]
        played = bool(len(this)) and float(this.iloc[0]) > 0
        dnp = "did not" in str(r.get("practice_status") or "").lower()
        out.append((season, wk, normalize_id(r["gsis_id"]), round(avg, 1), dnp, played))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2027)))
    ap.add_argument("--crosswalk", type=Path, default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--cache-dir", type=Path, default=RESEARCH_CACHE / "nflreadpy")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=args.cache_dir,
                  cache_duration=365 * 86400, verbose=False)
    cw = Crosswalk.from_csv(args.crosswalk)
    rows = [x for s in args.seasons for x in season_rows(s, cw)]
    df = pd.DataFrame(rows, columns=["season", "week", "gsis", "snap", "dnp", "played"])
    df = df.drop_duplicates(["season", "week", "gsis"]).assign(sat=lambda d: ~d["played"])

    def line(x: pd.DataFrame, label: str) -> None:
        rate = f"{100 * x['sat'].mean():.1f}%" if len(x) else "n/a"
        print(f"{label}: n={len(x)}, sat {int(x['sat'].sum())} ({rate})")

    for s, g in df.groupby("season"):
        line(g, f"{s} Questionable")
    for label, part in (("2019-2025", df.loc[df["season"] < 2026]),
                        ("2026", df.loc[df["season"] == 2026])):
        line(part, f"{label} all Questionable")
        line(part.loc[part["dnp"]], f"{label} Questionable + did not practise")
        line(part.loc[~part["dnp"]], f"{label} Questionable, practised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
