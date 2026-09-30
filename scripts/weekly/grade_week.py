"""grade_week.py — grade a finished week's advice against what actually happened.

    PYTHONPATH=src python scripts/weekly/grade_week.py --week 3 \
        --archives data/outputs/cloud/0056_.../ledger/decisions \
        --actuals-record data/outputs/cloud/0056_.../outputs/dashboard/dashboard_latest.json \
        [--before 2026-09-27T17:00:00Z] [--acted KEY ...] [--declined KEY ...]

Rule #7 says grade the choice, not the projection, and do it from numbers
frozen at decision time. `gridiron.decisions.grade_archive` already does
that; nothing ran it. This script:

  1. picks ONE archive for the week — the latest one built before `--before`
     (e.g. the Sunday early kickoff), or by default the latest built while
     the week was still pregame — and says which;
  2. reads actual league points for that week by gsis id — from nflverse
     directly (`--actuals-nflverse`, the most complete: every player with a
     stat line, and every team defense as `DEF:<team>`), a season cache (`--actuals-cache`), or a LATER record's
     usage block (`--actuals-record`, offline, but it only holds players who
     are still yours or still available) — never from the archive itself;
  3. prints every graded comparison (names — stdout only) and the shoot-outs
     (skill positions together; kickers and team defenses each apart), and
  4. upserts ONE aggregate row per (season, week, archive) into
     data/ledger/grades/season<YYYY>.csv — counts and rates only, no player
     names or ids — so accuracy accumulates across the season.

`--acted` / `--declined` take a comparison key exactly as printed
(`start_sit:FLEX:<held id>:<alternative id>`) and are the ONLY way a row
becomes an observed owner decision; the program never infers what the owner
did from the roster.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from gridiron.decisions import (ACTED, DECLINED, Grade, archive_stamp,
                                grade_archive, read_archive)
from gridiron.ids import nflverse_team
from gridiron.paths import LEDGER
from gridiron.trends import actuals_from_usage

GRADES_DIR = LEDGER / "grades"
FIELDS = ("season", "week", "archive", "archive_built", "graded", "ungradeable",
          "scorable", "agree", "direction_n", "direction_agree", "withheld",
          "unverified", "decisions", "projection_mae", "projection_n",
          "shootout_n", "shootout_pairs",
          "page_pairwise", "baseline_v1_pairwise", "sleeper_pairwise", "blend_pairwise",
          "stack_pairwise", "page_mae", "baseline_v1_mae", "sleeper_mae", "blend_mae",
          "stack_mae",
          # kickers and team defenses, graded apart (their points never
          # share a scale with a receiver's): pairs within the position
          "k_n", "k_pairs", "k_page_pairwise", "k_baseline_v1_pairwise",
          "k_sleeper_pairwise", "k_stack_pairwise",
          "def_n", "def_pairs", "def_page_pairwise", "def_sleeper_pairwise",
          "def_stack_pairwise", "graded_at")

#: The systems the weekly shoot-out scores on identical players:
#:   page         the projection the page used (advanced_v1 when it ran)
#:   baseline_v1  the model before advanced stats (record `contenders`)
#:   sleeper      Sleeper's pre-kickoff projection (record `shadow`)
#:   blend        (page + sleeper) / 2
#:   stack        advanced + Sleeper (record `contenders`)
#: A system an archive does not carry is simply absent from that week.
SYSTEMS = ("page", "baseline_v1", "sleeper", "blend", "stack")
#: Pairs are formed only among players the shadow projected at least this
#: high — the same "fantasy-relevant" cut as the 2025 backtest.
SHOOTOUT_MIN = 5.0
SKILL = ("QB", "RB", "WR", "TE")
#: K and DEF each form their own shoot-out; every starting kicker and
#: defense is fantasy-relevant, so no projection floor.
SPECIAL = {"k": ("K",), "def": ("DEF",)}


def direction(grade: Grade) -> tuple[int, int]:
    """(agree, n) over EVERY graded comparison — withheld and unverified ones
    included. This is the projection's directional hit rate, not the advice's:
    on game days the page withholds most comparisons (stale designations), so
    without it a season could pass with no measure of the model at all. It is
    stored and printed under its own name and never merged with `agree`."""
    rows = [c for c in grade.comparisons if c.status == "graded"
            and c.projected_edge is not None and c.realized_edge is not None
            and c.projected_edge != 0]
    return sum(1 for c in rows if (c.projected_edge > 0) == (c.realized_edge > 0)), len(rows)


def _time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def week_archives(root: Path, season: int, week: int) -> list[tuple[datetime, Path, dict]]:
    """Every readable archive of one week, oldest first. `root` may be the
    decisions dir or its season<YYYY> subdirectory."""
    folder = root / f"season{season}" if (root / f"season{season}").is_dir() else root
    out = []
    for path in folder.glob(f"week{week:02d}_*.json"):
        stamp = archive_stamp(path)
        try:
            blob = read_archive(path)
        except (OSError, ValueError):
            continue
        if stamp is not None and int(blob.get("week") or -1) == week:
            out.append((stamp, path, blob))
    return sorted(out, key=lambda x: (x[0], x[1].name))


def pick_archive(archives: Sequence[tuple[datetime, Path, dict]], *,
                 before: datetime | None) -> tuple[tuple[datetime, Path, dict] | None, str]:
    if before is not None:
        eligible = [a for a in archives if a[0] < before]
        why = f"the latest built before {before.isoformat()}"
    else:
        eligible = [a for a in archives if a[2].get("phase") == "pregame"]
        why = "the latest built while the week was still pregame"
    return (eligible[-1] if eligible else None), why


def actuals_from_record(path: Path, week: int) -> dict[str, float]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    return actuals_from_usage(blob.get("usage"), week)


def actuals_from_cache(season_dir: Path, season: int, week: int) -> dict[str, float]:
    """Actual points from a season cache (weekly_stats + snap_counts +
    crosswalk), scored by `gridiron.usage` — the local path."""
    from gridiron.ids import Crosswalk
    from gridiron.ingest import Manifest
    from gridiron.usage import player_weeks
    m = Manifest.load(season_dir, season)
    weekly = m.read_frame("weekly_stats")
    snaps = m.read_frame("snap_counts")
    cw = m.file("crosswalk")
    if weekly is None or cw is None:
        raise SystemExit(f"[grade] {season_dir} has no weekly_stats/crosswalk")
    frame = player_weeks(weekly, snaps, Crosswalk.from_csv(cw))
    rows = frame.loc[frame["week"] == week]
    return {str(g): float(p) for g, p in zip(rows["gsis_id"], rows["league_points"])
            if g and p == p}


def actuals_from_nflverse(season: int, week: int) -> dict[str, float]:
    """Actual points for EVERY player with a stat line that week, straight from
    nflverse (public, read-only), scored by `gridiron.usage.score_frame`. The
    most complete source: an add claimed by another manager since still has
    a line here, where a later record's usage block no longer holds him.
    Team defenses are keyed `DEF:<team>` (`gridiron.scoring.defense_points`
    via `gridiron.models.advanced.defense_history`)."""
    import nflreadpy as nfl
    from gridiron.usage import score_frame
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    rows = score_frame(weekly.loc[weekly["week"] == week])
    out = {str(g): float(p) for g, p in zip(rows["player_id"], rows["league_points"])
           if g and p == p}
    try:
        from gridiron.models.advanced import defense_history
        dh = defense_history(nfl.load_team_stats([season], summary_level="week").to_pandas(),
                             nfl.load_schedules([season]).to_pandas())
    except Exception as exc:                                   # noqa: BLE001
        print(f"[grade] team defenses not graded: {exc}", file=sys.stderr)
        return out
    return {**out, **defense_actuals(dh, week)}


def defense_actuals(dhist, week: int) -> dict[str, float]:
    """`DEF:<team>` -> league DEF points, only for games with a final score."""
    wk = dhist.loc[(dhist["week"] == week) & dhist["points_allowed"].notna()]
    return {f"DEF:{t}": float(p) for t, p in zip(wk["team"], wk["dst_points"])}


def actual_key(p: Mapping[str, object]) -> str:
    """The actuals key for one record entry: gsis id, or `DEF:<team>`."""
    if str(p.get("position") or "") in ("DEF", "DST"):
        team = nflverse_team(p.get("team") or p.get("sleeper_id") or p.get("id") or "")
        return f"DEF:{team}" if team else ""
    return str(p.get("gsis_id") or "")


def shootout(archive: Mapping[str, object], actuals: Mapping[str, float], *,
             min_proj: float = SHOOTOUT_MIN,
             positions: Sequence[str] = SKILL) -> dict | None:
    """Every system the archive carries, on the SAME players, against actual
    points: MAE, and the start/sit success rate — over every pair at one
    position (both Sleeper-projected >= `min_proj`, actuals not tied), the
    share each system ordered the way the points did.

    Scored players: Sleeper number captured before their kickoff, a page
    projection that was not withheld, an actual, and a number from every
    system present. Returns None when the archive carries no shadow."""
    shadow = archive.get("shadow") or {}
    sp = shadow.get("players") if isinstance(shadow, Mapping) else None
    if not sp:
        return None
    people = [(p.get("sleeper_id"), p) for p in archive.get("roster") or []]
    radar = archive.get("radar") or {}
    people += [(c.get("id"), c) for c in radar.get("candidates") or []]
    people = [(sid, p) for sid, p in people if str(p.get("position")) in positions]
    group = {str(sid or "") for sid, _ in people}
    cont = archive.get("contenders") or {}
    # a contender joins when it covers this position group at all (the old
    # baseline never projected a team defense, so it sits DEF out)
    extra = {k: cont.get(k) or {} for k in ("baseline_v1", "stack")
             if isinstance(cont, Mapping) and group & set(cont.get(k) or {})}
    systems = ["page"] + [k for k in ("baseline_v1",) if k in extra] + ["sleeper", "blend"] \
        + [k for k in ("stack",) if k in extra]
    rows, seen = [], set()
    for sid, p in people:
        sid = str(sid or "")
        entry = sp.get(sid) if isinstance(sp, Mapping) else None
        if not sid or sid in seen or not isinstance(entry, Mapping):
            continue
        seen.add(sid)
        ours, theirs = p.get("projected"), entry.get("points")
        actual = actuals.get(actual_key(p))
        if (not entry.get("pre_kickoff") or p.get("withheld") or ours is None
                or theirs is None or actual is None):
            continue
        row = {"position": p.get("position"), "actual": float(actual),
               "page": float(ours), "sleeper": float(theirs),
               "blend": (float(ours) + float(theirs)) / 2.0}
        for k, table in extra.items():
            row[k] = table.get(sid)
        if any(row.get(k) is None for k in systems):
            continue
        rows.append(row)
    if not rows:
        return {"n": 0, "pairs": 0, "systems": systems}
    out: dict = {"n": len(rows), "systems": systems}
    for sname in systems:
        out[f"{sname}_mae"] = round(sum(abs(r[sname] - r["actual"]) for r in rows)
                                    / len(rows), 3)
    hits, pairs = dict.fromkeys(systems, 0), 0
    rel = [r for r in rows if r["sleeper"] >= min_proj]
    for i, a in enumerate(rel):
        for b in rel[i + 1:]:
            if a["position"] != b["position"] or a["actual"] == b["actual"]:
                continue
            pairs += 1
            truth = a["actual"] > b["actual"]
            for sname in systems:
                if a[sname] != b[sname] and (a[sname] > b[sname]) == truth:
                    hits[sname] += 1
    out["pairs"] = pairs
    for sname in systems:
        out[f"{sname}_pairwise"] = round(hits[sname] / pairs, 4) if pairs else None
    return out


def special_shootouts(archive: Mapping[str, object],
                      actuals: Mapping[str, float]) -> dict[str, dict]:
    """The K and DEF shoot-outs, each within its own position."""
    out = {}
    for g, positions in SPECIAL.items():
        res = shootout(archive, actuals, min_proj=0.0, positions=positions)
        if res:
            out[g] = res
    return out


def aggregate(grade: Grade, season: int, archive: Path, built: datetime,
              shoot: Mapping[str, object] | None = None,
              special: Mapping[str, Mapping[str, object]] | None = None) -> dict:
    agree, n = grade.agreement()
    d_agree, d_n = direction(grade)
    extra = {}
    for f in FIELDS:
        g, _, key = f.partition("_")
        if g in SPECIAL and key:
            v = ((special or {}).get(g) or {}).get(key)
            extra[f] = "" if v is None else v
    return {"season": season, "week": grade.week, "archive": archive.name,
            "archive_built": built.isoformat(timespec="seconds"),
            "graded": sum(1 for c in grade.comparisons if c.status == "graded"),
            "ungradeable": grade.ungradeable, "scorable": n, "agree": agree,
            "direction_n": d_n, "direction_agree": d_agree,
            "withheld": len(grade.withheld), "unverified": len(grade.unverified),
            "decisions": len(grade.decisions),
            "projection_mae": "" if grade.projection_mae is None else grade.projection_mae,
            "projection_n": grade.projection_n,
            "shootout_n": (shoot or {}).get("n", ""),
            "shootout_pairs": (shoot or {}).get("pairs", ""),
            **{f"{s}_{m}": "" if (shoot or {}).get(f"{s}_{m}") is None
               else shoot[f"{s}_{m}"] for s in SYSTEMS for m in ("pairwise", "mae")},
            **extra,
            "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def upsert(path: Path, row: Mapping[str, object]) -> list[dict]:
    """Replace the row for the same (season, week, archive); keep the rest."""
    rows: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            rows = [r for r in csv.DictReader(fh)
                    if (r["season"], r["week"], r["archive"])
                    != (str(row["season"]), str(row["week"]), str(row["archive"]))]
    rows = [{k: r.get(k, "") for k in FIELDS} for r in rows]
    rows.append({k: row[k] for k in FIELDS})
    rows.sort(key=lambda r: (int(r["season"]), int(r["week"]), r["archive"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def season_line(rows: Sequence[Mapping[str, object]]) -> str:
    agree = sum(int(r["agree"]) for r in rows)
    n = sum(int(r["scorable"]) for r in rows)
    maes = [(float(r["projection_mae"]), int(r["projection_n"])) for r in rows
            if str(r["projection_mae"]) not in ("", "None")]
    mae = (sum(m * k for m, k in maes) / sum(k for _, k in maes)) if maes else None
    d_agree = sum(int(r.get("direction_agree") or 0) for r in rows)
    d_n = sum(int(r.get("direction_n") or 0) for r in rows)
    return (f"season to date over {len(rows)} graded archive(s): endorsed advice "
            f"pointed the right way {agree}/{n}"
            + (f" ({agree / n:.0%})" if n else "")
            + f"; projection direction on every graded comparison {d_agree}/{d_n}"
            + (f" ({d_agree / d_n:.0%})" if d_n else "")
            + (f"; roster projection MAE {mae:.2f}" if mae is not None else "")
            + shootout_line(rows))


def shootout_line(rows: Sequence[Mapping[str, object]]) -> str:
    """Season-to-date shoot-out, pairs-weighted, over weeks that have one."""
    done = [r for r in rows if str(r.get("shootout_pairs") or "0") not in ("", "0")]
    if not done:
        return "; no shoot-out yet (archives carry no shadow projections)"
    pairs = sum(int(r["shootout_pairs"]) for r in done)
    rate = {}
    for sname in SYSTEMS:
        have = [r for r in done if str(r.get(f"{sname}_pairwise") or "") not in ("", "None")]
        n = sum(int(r["shootout_pairs"]) for r in have)
        if n:
            rate[sname] = sum(float(r[f"{sname}_pairwise"]) * int(r["shootout_pairs"])
                              for r in have) / n
    line = ("; start/sit shoot-out over " + f"{pairs} pairs in {len(done)} week(s): "
            + ", ".join(f"{k} {v:.1%}" for k, v in rate.items()))
    for g in SPECIAL:
        have = [r for r in rows if str(r.get(f"{g}_pairs") or "0") not in ("", "0")]
        if not have:
            continue
        n = sum(int(r[f"{g}_pairs"]) for r in have)
        parts = []
        for sname in SYSTEMS:
            got = [r for r in have if str(r.get(f"{g}_{sname}_pairwise") or "") not in ("", "None")]
            k = sum(int(r[f"{g}_pairs"]) for r in got)
            if k:
                parts.append(f"{sname} {sum(float(r[f'{g}_{sname}_pairwise']) * int(r[f'{g}_pairs']) for r in got) / k:.1%}")
        line += f"; {g.upper()} over {n} pairs: " + ", ".join(parts)
    return line


def render(grade: Grade, names: Mapping[str, str]) -> list[str]:
    out = ["| kind | slot | held | alternative | projected edge | actual edge | status | key |",
           "|---|---|---|---|---|---|---|---|"]
    for c in grade.comparisons:
        out.append(f"| {c.kind} | {c.slot} | {names.get(c.held_id, c.held_id)} | "
                   f"{names.get(c.alternative_id, c.alternative_id)} | "
                   f"{c.projected_edge if c.projected_edge is not None else '—'} | "
                   f"{c.realized_edge if c.realized_edge is not None else '—'} | "
                   f"{c.status} ({c.label()}) | `{c.key}` |")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--archives", type=Path, default=LEDGER / "decisions")
    ap.add_argument("--archive", type=Path, default=None, help="grade this file")
    ap.add_argument("--before", default=None, help="ISO time; latest archive before it")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--actuals-record", type=Path)
    src.add_argument("--actuals-cache", type=Path, help="a season<YYYY> cache dir")
    src.add_argument("--actuals-nflverse", action="store_true",
                     help="score the week from nflverse directly (most complete)")
    ap.add_argument("--acted", action="append", default=[])
    ap.add_argument("--declined", action="append", default=[])
    ap.add_argument("--grades-dir", type=Path, default=GRADES_DIR)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    if args.archive:
        blob = read_archive(args.archive)
        chosen = (archive_stamp(args.archive) or datetime.now(timezone.utc),
                  args.archive, blob)
        why = "given with --archive"
    else:
        season = args.season or datetime.now(timezone.utc).year
        archives = week_archives(args.archives, season, args.week)
        chosen, why = pick_archive(archives, before=_time(args.before) if args.before else None)
        if chosen is None:
            print(f"[grade] no week {args.week} archive under {args.archives} "
                  f"qualifies ({why}); {len(archives)} archive(s) for the week",
                  file=sys.stderr)
            return 2
    built, path, archive = chosen
    season = int(archive.get("season") or args.season or built.year)
    if args.actuals_record:
        actuals = actuals_from_record(args.actuals_record, args.week)
    elif args.actuals_cache:
        actuals = actuals_from_cache(args.actuals_cache, season, args.week)
    else:
        actuals = actuals_from_nflverse(season, args.week)
    if not actuals:
        print(f"[grade] no actual points for week {args.week} in the actuals source "
              f"(a record's usage block covers only its last 4 weeks)", file=sys.stderr)
        return 3
    observed = {**{k: ACTED for k in args.acted}, **{k: DECLINED for k in args.declined}}
    grade = grade_archive(archive, actuals, observed=observed)
    names = {str(p.get("sleeper_id")): str(p.get("name")) for p in archive.get("roster") or []}
    for up in archive.get("upgrades") or []:
        add = up.get("add") or {}
        names[str(add.get("sleeper_id"))] = str(add.get("name"))
    print(f"# Week {args.week} grade — archive {path.name} (built {built.isoformat()}; "
          f"{why})\n")
    d_agree, d_n = direction(grade)
    shoot = shootout(archive, actuals)
    print(grade.summary())
    print(f"projection direction on every graded comparison (advice or not): "
          f"{d_agree}/{d_n}")
    if shoot and shoot.get("pairs"):
        print(f"shoot-out on {shoot['n']} players / {shoot['pairs']} start/sit pairs: "
              + ", ".join(f"{s} {shoot[f'{s}_pairwise']:.1%} (MAE {shoot[f'{s}_mae']:.2f})"
                          for s in shoot["systems"]) + "\n")
    else:
        print("shoot-out: this archive carries no pre-kickoff shadow projections\n")
    special = special_shootouts(archive, actuals)
    for g, res in special.items():
        if res.get("pairs"):
            print(f"{g.upper()} shoot-out on {res['n']} / {res['pairs']} pairs: "
                  + ", ".join(f"{s} {res[f'{s}_pairwise']:.1%}" for s in res["systems"]))
    print("\n".join(render(grade, names)) + "\n")
    for n in grade.notes:
        print(f"- {n}")
    if not args.no_save:
        rows = upsert(args.grades_dir / f"season{season}.csv",
                      aggregate(grade, season, path, built, shoot, special))
        print(f"\n{season_line(rows)}")
        print(f"[grade] saved to {args.grades_dir / f'season{season}.csv'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
