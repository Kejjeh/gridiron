"""Assemble data/reference/play_callers.csv: who called plays, team-season by team-season.

    PYTHONPATH=src python scripts/research/build_play_callers.py \\
        --research A.json [B.json ...] [--hc-changes C.json] [--check]

Workstream B of docs/research/MODEL_ROADMAP.md. One row per (season, team,
role, person, week range), 2019-2026:

  HC   head coach. From nflverse schedules (`home_coach`/`away_coach`), which
       carry ONE coach per team-season in some seasons even when a coach was
       fired mid-season, so in-season changes come only from sourced rows
       (`--hc-changes`), which then split the nflverse range.
  OPC  the offensive play-caller: who called the plays in games, only where
       a public source says so explicitly.
  OC   the offensive coordinator title (not necessarily the play-caller).
  DC   the defensive coordinator title.

Research rows come from the sourced-research workflows: each row carries the
URL a researcher opened and a verbatim quote. A row enters the table only
when it is confirmed: scripts/research/check_sources.py found the quote on
the page with the role's words and the person's name, or a second agent
re-read the source, or a person read it (`--manual`); `basis` says which.
Rows nobody could source are absent; a consumer treats absence as unknown,
never as "the OC called plays". `basis` says which kind each row is.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pandas as pd

from gridiron.paths import REPO_ROOT, RESEARCH_CACHE

OUT = REPO_ROOT / "data" / "reference" / "play_callers.csv"
SEASONS = tuple(range(2019, 2027))
TEAMS = ("ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE "
         "NO NYG NYJ PHI PIT SEA SF TB TEN WAS").split()
FIELDS = ("season", "team", "role", "name", "week_from", "week_to", "basis", "source", "quote")
LAST_WEEK = {2019: 17, 2020: 17}


def last_week(season: int) -> int:
    return LAST_WEEK.get(season, 18)


def nflverse_hc() -> list[dict]:
    import nflreadpy as nfl
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    s = nfl.load_schedules(list(SEASONS)).to_pandas()
    s = s.loc[s["game_type"] == "REG"].replace({"home_team": {"OAK": "LV"}, "away_team": {"OAK": "LV"}})
    rows = pd.concat([
        s[["season", "week", "home_team", "home_coach"]].rename(columns={"home_team": "team",
                                                                         "home_coach": "name"}),
        s[["season", "week", "away_team", "away_coach"]].rename(columns={"away_team": "team",
                                                                         "away_coach": "name"})])
    rows = rows.loc[rows["name"].notna()]
    out = []
    for (season, team, name), g in rows.groupby(["season", "team", "name"]):
        out.append({"season": int(season), "team": team, "role": "HC", "name": str(name),
                    "week_from": int(g["week"].min()), "week_to": int(g["week"].max()),
                    "basis": "nflverse schedules", "source": "nflreadpy.load_schedules home_coach/away_coach",
                    "quote": ""})
    return out


def _checks(paths) -> dict:
    """(season, index) -> the mechanical check (scripts/research/check_sources.py)."""
    out = {}
    for p in paths or []:
        for r in json.loads(Path(p).read_text(encoding="utf-8")):
            out[(int(r["season"]), int(r["index"]))] = r
    return out


def _manual(path) -> dict:
    """(season, team, role, name) -> {"accept": bool, "why": str}: a person read
    the cited page (rows the mechanical check could not confirm)."""
    if not path:
        return {}
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(int(r["season"]), r["team"], r["role"], r["name"]): r for r in rows}


def checked_rows(paths, checks=None, manual=None) -> tuple[list[dict], list[dict]]:
    """(accepted rows, dropped rows with the reason) from workflow outputs.

    A row is accepted when a second reader confirmed it (`verdicts`, with no
    contradiction), or the mechanical check found its verbatim quote on the
    page with the role's words and the person's name, or a person read the
    page (`manual`). A contradiction from any reader drops it."""
    checks, manual = _checks(checks), _manual(manual)
    keep, dropped = [], []
    for p in paths:
        blob = json.loads(Path(p).read_text(encoding="utf-8"))
        blob = blob.get("result", blob)
        for item in blob.get("results", []):
            res, verdicts = item.get("research") or {}, item.get("verdicts")
            season = int(item.get("season") or res.get("season"))
            by_index = {v["index"]: v for v in (verdicts or [])}
            for i, r in enumerate(res.get("rows") or []):
                row = {"season": season, "team": r["team"], "role": r["role"], "name": r["name"].strip(),
                       "week_from": int(r["week_from"]), "week_to": int(r["week_to"])}
                v, c = by_index.get(i), checks.get((season, i))
                m = manual.get((season, r["team"], r["role"], row["name"]))
                if v and v.get("contradiction"):
                    dropped.append({**row, "why": f"a reader found a contradiction: {v['contradiction']}"})
                    continue
                if m is not None and not m.get("accept"):
                    dropped.append({**row, "why": f"read by hand: {m.get('why')}"})
                    continue
                url = (v or {}).get("new_source_url") or r["source_url"]
                quote = (v or {}).get("new_quote") or r["quote"]
                basis = []
                if c and c.get("passes"):
                    basis.append("quote checked on the page")
                if v and v.get("supported"):
                    basis.append("re-read by a second agent")
                if m is not None and m.get("accept"):
                    basis.append(f"read by hand ({m.get('why')})")
                if not basis:
                    dropped.append({**row, "why": "not confirmed (" + (
                        "page not fetched" if c and not c.get("fetched") else
                        "quote not on the page" if c and not c.get("quote_found") else
                        "role words missing from the quote" if c and not c.get("role_words") else
                        "name not on the page" if c else "no check") + ")"})
                    continue
                keep.append({**row, "basis": "sourced; " + "; ".join(basis), "source": url,
                             "quote": quote})
    return keep, dropped


def apply_hc_changes(hc: list[dict], path: str | None,
                     checks: str | None = None) -> tuple[list[dict], list[dict]]:
    if not path:
        return hc, []
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    blob = blob.get("result", blob)
    changes = (blob.get("research") or {}).get("changes") or []
    verdicts = {v["index"]: v for v in ((blob.get("verify") or {}).get("verdicts") or [])}
    mech = {int(r["index"]): r for r in json.loads(Path(checks).read_text(encoding="utf-8"))} \
        if checks else {}
    applied, dropped = [], []
    out = list(hc)
    for i, c in enumerate(changes):
        v, m = verdicts.get(i), mech.get(i)
        if v is not None and (not v.get("supported") or v.get("correction")):
            dropped.append({**c, "why": "not confirmed" if not v.get("supported")
                            else f"checker correction: {v['correction']}"})
            continue
        if v is None and not (m and m.get("passes")):
            dropped.append({**c, "why": "not confirmed (no reader, mechanical check failed)"})
            continue
        season, team, first = int(c["season"]), c["team"], int(c["first_week_incoming"])
        url = (v or {}).get("new_source_url") or c["source_url"]
        quote = (v or {}).get("new_quote") or c["quote"]
        # the outgoing coach's rows end the week before; the incoming coach's begin
        kept = [r for r in out if not (r["season"] == season and r["team"] == team and r["role"] == "HC")]
        prior = [r for r in out if r["season"] == season and r["team"] == team and r["role"] == "HC"]
        start = min((r["week_from"] for r in prior), default=1)
        end = max((r["week_to"] for r in prior), default=last_week(season))
        basis = "sourced; " + ("re-read by a second agent" if v else "quote checked on the page")
        kept.append({"season": season, "team": team, "role": "HC", "name": c["outgoing"].strip(),
                     "week_from": start, "week_to": first - 1, "basis": basis,
                     "source": url, "quote": quote})
        kept.append({"season": season, "team": team, "role": "HC", "name": c["incoming"].strip(),
                     "week_from": first, "week_to": end, "basis": basis,
                     "source": url, "quote": quote})
        out = kept
        applied.append(c)
    return out, dropped


def validate(rows: list[dict]) -> list[str]:
    problems = []
    for r in rows:
        if r["team"] not in TEAMS:
            problems.append(f"unknown team {r['team']} ({r['season']} {r['role']} {r['name']})")
        if r["role"] not in ("HC", "OPC", "OC", "DC"):
            problems.append(f"unknown role {r['role']}")
        if not (1 <= r["week_from"] <= r["week_to"] <= last_week(r["season"])):
            problems.append(f"bad weeks {r['season']} {r['team']} {r['role']} {r['name']}: "
                            f"{r['week_from']}-{r['week_to']}")
    df = pd.DataFrame(rows)
    for (s, t, role), g in df.groupby(["season", "team", "role"]):
        weeks = []
        for a, b in zip(g["week_from"], g["week_to"]):
            weeks += list(range(int(a), int(b) + 1))
        # one head coach and one play-caller at a time; coordinator TITLES can
        # be shared (co-coordinators), so overlapping OC/DC rows are allowed
        if role in ("HC", "OPC") and len(weeks) != len(set(weeks)):
            problems.append(f"overlapping {role} rows for {t} {s}: {sorted(g['name'])}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--research", nargs="*", default=[])
    ap.add_argument("--hc-changes", default=None)
    ap.add_argument("--hc-checks", default=None, help="check_sources.py --kind hc output")
    ap.add_argument("--checks", nargs="*", default=[], help="check_sources.py outputs")
    ap.add_argument("--manual", default=None, help="rows a person read: accept or reject")
    ap.add_argument("--check", action="store_true", help="validate and report; write nothing")
    args = ap.parse_args(argv)
    hc, hc_dropped = apply_hc_changes(nflverse_hc(), args.hc_changes, args.hc_checks)
    research, dropped = checked_rows(args.research, args.checks, args.manual)
    rows = sorted(hc + research, key=lambda r: (r["season"], r["team"], r["role"], r["week_from"]))
    problems = validate(rows)
    for p in problems:
        print(f"PROBLEM {p}", file=sys.stderr)
    cov = pd.DataFrame(rows).groupby(["season", "role"])["team"].nunique().unstack(fill_value=0)
    print("team-seasons covered (of 32):")
    print(cov.to_string())
    print(f"{len(research)} research rows confirmed, {len(dropped)} dropped; "
          f"{len(hc_dropped)} head-coach changes dropped")
    for d in dropped:
        print(f"  dropped {d['season']} {d['team']} {d['role']} {d['name']}: {d['why']}")
    for d in hc_dropped:
        print(f"  dropped HC change {d.get('season')} {d.get('team')}: {d['why']}")
    if args.check or problems:
        return 1 if problems else 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT.relative_to(REPO_ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
