"""odds_names.py — grow the odds-name table from LOCAL snapshots. Prints names:
run it on your own machine, never in a workflow (the logs there are public).

    PYTHONPATH=src python scripts/ingest/odds_names.py propose [--week 9] [--dry-run]
    PYTHONPATH=src python scripts/ingest/odds_names.py check

Owner decision 1 (2026-11-02), rule #3. An odds provider sends a player's
NAME. It becomes a gsis id only through the committed table
data/reference/odds_player_names.csv (`gridiron.props.IdentityTable`), and
`propose` is the only machine that writes to it:

* For every name in a local snapshot (data/research/cache/props/raw/) or a
  local name list (raw/names/, written by `pull_props.py names` at 1 credit
  a game) that the table does not resolve on that event's two teams, it
  lists the candidate players on those two teams that week (nflverse
  weekly rosters), for a person to check.
* It APPENDS a row only when the match is 'exact': the name equals, string
  for string, the weekly-roster `full_name` of exactly one player on one of
  the event's two teams that week, and that player's position is consistent
  with every market the name was priced in. Nothing else: no fuzzy match,
  no case folding, no suffix ("Jr.", "III") stripped, no nickname. A roster
  that is not published for the snapshot's week yet proposes nothing.
* A person reviews the table and marks a row 'reviewed' (or adds one by hand
  for a name the machine cannot match, e.g. a different spelling). Record-
  only numbers may use 'exact' rows; anything that SERVES needs 'reviewed'
  (`IdentityTable.serving_id` refuses the rest).

`check` validates the table and prints its counts by basis.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from gridiron import props as P
from gridiron.ids import normalize_id
from gridiron.league_config import SEASON_YEAR


def load_rosters(season: int, path: Path | None = None):
    """nflverse weekly rosters (team, week, full_name, gsis_id, position)."""
    import pandas as pd
    if path is not None:
        return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    import nflreadpy as nfl
    return nfl.load_rosters_weekly([season]).to_pandas()


def roster_index(rosters, week: int, teams: Sequence[str]) -> list[dict]:
    """Every roster row of those teams that week, as plain dicts."""
    if rosters is None or len(rosters) == 0:
        return []
    wk = rosters.loc[(rosters["week"] == int(week)) & rosters["team"].isin(list(teams))]
    out = []
    for r in wk.itertuples():
        gid = normalize_id(getattr(r, "gsis_id", ""))
        if gid:
            out.append({"full_name": str(getattr(r, "full_name", "") or ""), "gsis_id": gid,
                        "team": str(r.team), "position": str(getattr(r, "position", "") or "")})
    return out


def names_by_event(payload: object) -> tuple[tuple[str, ...], dict[str, set[str]]]:
    """The event's two nflverse teams and each name it priced -> its markets."""
    rows, _ = P.parse_event_odds(payload)
    teams = tuple(t for t in P.event_teams(payload if isinstance(payload, Mapping) else {})
                  if t)
    markets: dict[str, set[str]] = {}
    for r in rows:
        markets.setdefault(r.player, set()).add(r.market)
    return teams, markets


def exact_match(name: str, markets: set[str], roster: Sequence[Mapping[str, str]]
                ) -> tuple[dict | None, str]:
    """The one roster player this name is EXACTLY, or (None, why not)."""
    hits = {r["gsis_id"]: r for r in roster if r["full_name"] == name}
    if not hits:
        return None, "no player of that exact name on either team"
    if len(hits) > 1:
        return None, "more than one player of that exact name on the two teams"
    (player,) = hits.values()
    wrong = sorted(m for m in markets if player["position"] not in P.MARKETS[m].positions)
    if wrong:
        return None, f"position {player['position'] or '?'} not consistent with {', '.join(wrong)}"
    return player, ""


def candidates(markets: set[str], roster: Sequence[Mapping[str, str]]) -> list[dict]:
    """Players on the two teams at a position consistent with every market
    (for a person to read; nothing is chosen for them)."""
    ok = [r for r in roster if all(r["position"] in P.MARKETS[m].positions for m in markets)]
    uniq = {r["gsis_id"]: r for r in ok}
    return sorted(uniq.values(), key=lambda r: (r["team"], r["position"], r["full_name"]))


def propose(*, season: int, raw_root: Path, table_path: Path, rosters, weeks: Sequence[int] | None,
            today: str, dry_run: bool, out=sys.stdout) -> list[P.IdentityRow]:
    table = P.IdentityTable.read(table_path)
    # snapshots and the cheaper name lists alike: every payload of a game
    sources: dict[tuple[int, str], list[object]] = {}
    for game_key, (_path, blob) in P.read_raw_snapshots(raw_root, season).items():
        sources.setdefault(game_key, []).append(blob.get("payload"))
    for game_key, files in P.read_name_files(raw_root, season).items():
        sources.setdefault(game_key, []).extend(blob.get("payload") for _p, blob in files)
    roster_weeks = set() if rosters is None or len(rosters) == 0 else \
        {int(w) for w in rosters["week"].dropna().unique()}
    new: dict[tuple[str, str], P.IdentityRow] = {}
    for (week, gid), payloads in sorted(sources.items()):
        if weeks and week not in weeks:
            continue
        teams: tuple[str, ...] = ()
        priced: dict[str, set[str]] = {}
        for payload in payloads:
            event_teams, event_names = names_by_event(payload)
            teams = teams or event_teams
            for name, markets in event_names.items():
                priced.setdefault(name, set()).update(markets)
        open_names = {n: m for n, m in priced.items()
                      if table.resolve(n, teams).gsis_id is None}
        if not open_names:
            continue
        published = week in roster_weeks
        use_week = week if published else max((w for w in roster_weeks if w < week), default=None)
        roster = roster_index(rosters, use_week, teams) if use_week is not None else []
        print(f"\n## {gid} (week {week}): {len(open_names)} name(s) the table does not resolve"
              + ("" if published else f" — week {week} roster NOT published"
                 + (f"; candidates from week {use_week}, nothing appended" if use_week
                    else "; no roster to list candidates from")), file=out)
        for name, markets in sorted(open_names.items()):
            player, why = exact_match(name, markets, roster) if published else (None, "")
            if player is not None:
                key = (name, player["team"])
                new.setdefault(key, P.IdentityRow(name, player["team"], player["gsis_id"],
                                                  P.EXACT, today))
                print(f"- {name!r} [{', '.join(sorted(markets))}] -> EXACT "
                      f"{player['gsis_id']} {player['team']} {player['position']}", file=out)
                continue
            print(f"- {name!r} [{', '.join(sorted(markets))}]: "
                  f"{why or 'not checked (no roster for the week)'}; candidates:", file=out)
            for c in candidates(markets, roster):
                print(f"    {c['gsis_id']}  {c['team']:<3} {c['position']:<2}  {c['full_name']}",
                      file=out)
    rows = [r for k, r in sorted(new.items(), key=lambda kv: (kv[0][1], kv[0][0]))
            if not table.has(*k)]
    print(f"\n{len(rows)} exact row(s) {'would be ' if dry_run else ''}appended to "
          f"{table_path.name}", file=out)
    if rows and not dry_run:
        P.IdentityTable([*table.rows(), *rows])           # validate before writing
        with table_path.open("a", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            for r in rows:
                w.writerow([r.odds_name, r.team, r.gsis_id, r.basis, r.added])
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("propose", "check"))
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--week", type=int, action="append", default=None,
                    help="only snapshots of this week (repeatable); default every week")
    ap.add_argument("--raw-root", type=Path, default=P.RAW_ROOT)
    ap.add_argument("--table", type=Path, default=P.IDENTITY_PATH)
    ap.add_argument("--rosters", type=Path, default=None,
                    help="a weekly-roster parquet/csv instead of fetching nflverse")
    ap.add_argument("--dry-run", action="store_true", help="list, append nothing")
    args = ap.parse_args(argv)
    if args.command == "check":
        table = P.IdentityTable.read(args.table)
        by = {b: sum(1 for r in table.rows() if r.basis == b) for b in P.BASES}
        print(f"{args.table.name}: {len(table)} row(s); " + ", ".join(f"{b} {n}"
                                                                    for b, n in by.items()))
        return 0
    rosters = load_rosters(args.season, args.rosters)
    propose(season=args.season, raw_root=args.raw_root, table_path=args.table, rosters=rosters,
            weeks=args.week, today=date.today().isoformat(), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
