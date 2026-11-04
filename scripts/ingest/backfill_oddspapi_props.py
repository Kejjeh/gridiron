"""Backfill historical NFL player props from OddsPapi.

    PYTHONPATH=src python scripts/ingest/backfill_oddspapi_props.py probe
    PYTHONPATH=src python scripts/ingest/backfill_oddspapi_props.py backfill \
        --bookmakers pinnacle,draftkings,fanduel --from 2026-01-01

Runs where GRIDIRON_ODDSPAPI_KEY is set — in practice the dispatch-only
workflow .github/workflows/oddspapi-backfill.yml, because the key lives in
GitHub secrets. Read-only GETs. Output goes under
data/research/cache/oddspapi/ (gitignored, rule #10):

    catalogue.json             sport/tournament ids, prop-market catalogue
    fixtures.json              finished NFL fixtures in the window
    raw/<fixture>__<books>.json.gz   one historical-odds payload per call
    props_long.csv.gz          one row per price observation, prop markets only

Resumable: a raw payload already on disk is not fetched again (finished
fixtures do not change), so a re-run after a timeout continues where the
last one stopped. /v4/historical-odds is free; the discovery calls
(sports, tournaments, markets, bookmakers, fixtures) are metered — about
six requests per run, plus one if --player-names is given.

Logs print counts and ids only, never the key (gridiron.oddspapi.redact).
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron import oddspapi as op
from gridiron.config import get_settings
from gridiron.paths import RESEARCH_CACHE

OUT = RESEARCH_CACHE / "oddspapi"
BOOKS_RE = re.compile(r"^[a-z0-9_.\-]+(,[a-z0-9_.\-]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_BOOKS = "pinnacle,draftkings,fanduel"


def parse_books(raw: str) -> list[str]:
    raw = (raw or "").strip().lower()
    if not BOOKS_RE.fullmatch(raw):
        raise SystemExit(f"REFUSING bookmakers {raw!r}: comma-separated slugs only")
    books = list(dict.fromkeys(raw.split(",")))
    if len(books) > 9:
        raise SystemExit("REFUSING more than 9 bookmakers (3 calls per fixture)")
    return books


def parse_day(raw: str | None, default: str) -> str:
    raw = (raw or "").strip() or default
    if not DATE_RE.fullmatch(raw):
        raise SystemExit(f"REFUSING date {raw!r}: use YYYY-MM-DD")
    date.fromisoformat(raw)
    return raw


def client() -> op.Client:
    key = get_settings().oddspapi_key
    if not key:
        raise SystemExit("GRIDIRON_ODDSPAPI_KEY is not set (it lives in GitHub secrets; "
                         "run the oddspapi-backfill workflow)")
    return op.Client(key)


def chunks(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def discover(c: op.Client) -> dict:
    sport_id = op.find_american_football(c.sports())
    nfl_id = op.find_nfl(c.tournaments(sport_id))
    props = op.prop_markets(c.markets(), sport_id)
    return {"sport_id": sport_id, "tournament_id": nfl_id,
            "prop_markets": {str(k): v for k, v in props.items()},
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def props_of(catalogue: dict) -> dict[int, dict]:
    return {int(k): {**v, "outcomes": {int(o): n for o, n in v["outcomes"].items()}}
            for k, v in catalogue["prop_markets"].items()}


def fetch_fixtures(c: op.Client, tournament_id: int, start: str, end: str) -> list[dict]:
    """Finished fixtures, pulled in 30-day windows (range limits for the
    tournamentId form are undocumented; small windows are the safe side)."""
    seen: dict[str, dict] = {}
    cur = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    empty: list[str] = []
    while cur <= stop:
        nxt = min(cur + timedelta(days=30), stop + timedelta(days=1))
        try:
            got = c.fixtures(tournament_id, f"{cur}T00:00:00Z", f"{nxt}T00:00:00Z") or []
        except op.OddsPapiError as exc:
            if exc.status != 404:
                raise
            # The provider answers 404 for a window with no fixtures (e.g.
            # the NFL off-season). Logged, not swallowed.
            empty.append(f"{cur}..{nxt} ({exc.code or '404'})")
            got = []
        for f in got:
            seen[str(f["fixtureId"])] = f
        cur = nxt
    if empty:
        print(f"fixture windows with nothing (404): {empty}")
    if not seen:
        raise op.OddsPapiError(f"no finished NFL fixtures in {start}..{end}: every window 404'd")
    return sorted(seen.values(), key=lambda f: f.get("startTime") or "")


def check_books(c: op.Client, books: list[str]) -> list[str]:
    listing = json.dumps(c.bookmakers()).lower()
    return [b for b in books if f'"{b}"' not in listing]


def cmd_probe(args) -> int:
    c = client()
    acct = c.account()
    if isinstance(acct, dict):
        keep = {k: v for k, v in acct.items()
                if isinstance(v, (int, float)) or k.lower() in ("plan", "planname")}
        print(f"account (numeric fields): {keep}  keys={sorted(acct)}")
    cat = discover(c)
    print(f"american football sportId={cat['sport_id']}  NFL tournamentId={cat['tournament_id']}")
    print(f"player-prop markets in catalogue: {len(cat['prop_markets'])}")
    names: dict[str, int] = {}
    for m in cat["prop_markets"].values():
        names[str(m["name"])] = names.get(str(m["name"]), 0) + 1
    for n, k in sorted(names.items()):
        print(f"  {n}  ({k} line(s))")
    books = parse_books(args.bookmakers)
    missing = check_books(c, books)
    print(f"bookmakers requested={books} not-found-in-listing={missing}")
    start = parse_day(args.start, op.FIRST_COVERED_DATE)
    end = parse_day(args.end, date.today().isoformat())
    fixtures = fetch_fixtures(c, cat["tournament_id"], start, end)
    print(f"finished NFL fixtures {start}..{end}: {len(fixtures)}")
    if fixtures:
        f = fixtures[-1]
        payload = c.historical_odds(f["fixtureId"], books[:3])
        s = op.summarize(payload, props_of(cat))
        print(f"sample fixture {f['fixtureId']} {f.get('startTime')} "
              f"{f.get('participant1Abbr')} v {f.get('participant2Abbr')}: {s}")
    return 0


def raw_path(out: Path, fixture_id: str, books: list[str]) -> Path:
    return out / "raw" / f"{fixture_id}__{'+'.join(books)}.json.gz"


def cmd_backfill(args) -> int:
    books = parse_books(args.bookmakers)
    start = parse_day(args.start, op.FIRST_COVERED_DATE)
    end = parse_day(args.end, date.today().isoformat())
    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.max_minutes * 60

    c = client()
    cat = discover(c)
    (out / "catalogue.json").write_text(json.dumps(cat, indent=1), "utf-8")
    missing = check_books(c, books)
    if missing:
        print(f"WARNING: not in /v4/bookmakers listing: {missing} (fetching anyway)")
    fixtures = fetch_fixtures(c, cat["tournament_id"], start, end)
    (out / "fixtures.json").write_text(json.dumps(fixtures, indent=1), "utf-8")
    print(f"NFL sportId={cat['sport_id']} tournamentId={cat['tournament_id']} "
          f"prop markets={len(cat['prop_markets'])} fixtures={len(fixtures)} books={books}")

    if args.player_names:
        parts = c.participants(cat["sport_id"]) or {}
        (out / "participants.json").write_text(json.dumps(parts), "utf-8")

    fetched = skipped = failed = 0
    stopped_early = False
    for i, f in enumerate(fixtures[: args.max_fixtures or None]):
        for group in chunks(books, op.MAX_BOOKMAKERS_PER_HISTORICAL_CALL):
            p = raw_path(out, str(f["fixtureId"]), group)
            if p.exists():
                skipped += 1
                continue
            if time.monotonic() > deadline:
                stopped_early = True
                break
            try:
                payload = c.historical_odds(f["fixtureId"], group)
            except op.QuotaExhausted as exc:
                print(f"STOP: {exc}")
                stopped_early = True
                break
            except op.OddsPapiError as exc:
                failed += 1
                print(f"  fixture {f['fixtureId']} {group}: {exc}")
                continue
            tmp = p.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh)
            tmp.replace(p)
            fetched += 1
        if stopped_early:
            break
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(fixtures)} fixtures  fetched={fetched} cached={skipped}")
    print(f"historical calls: fetched={fetched} cached={skipped} failed={failed}"
          + ("  (STOPPED EARLY — re-run to resume)" if stopped_early else ""))

    rows = build_long(out, fixtures, props_of(cat))
    print(f"props_long.csv.gz: {rows} rows")
    return 1 if (failed and not fetched and not skipped) else 0


def build_long(out: Path, fixtures: list[dict], props: dict[int, dict]) -> int:
    by_id = {str(f["fixtureId"]): f for f in fixtures}
    names: dict[str, str] = {}
    pfile = out / "participants.json"
    if pfile.exists():
        raw = json.loads(pfile.read_text("utf-8"))
        if isinstance(raw, dict):
            names = {str(k): str(v) for k, v in raw.items()}
    rows: list[dict] = []
    for p in sorted((out / "raw").glob("*.json.gz")):
        fid = p.name.split("__", 1)[0]
        fixture = by_id.get(fid, {"fixtureId": fid})
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        rows.extend(op.flatten_props(payload, fixture, props, names))
    df = pd.DataFrame(rows, columns=op.PROP_COLUMNS)
    df.to_csv(out / "props_long.csv.gz", index=False, compression="gzip")
    summary = {
        "rows": len(df),
        "fixtures": int(df["fixture_id"].nunique()) if len(df) else 0,
        "players": int(df["oddspapi_player_id"].nunique()) if len(df) else 0,
        "named_players": int(df.loc[df["player_name"].notna(), "oddspapi_player_id"].nunique())
        if len(df) else 0,
        "by_market": df["market_name"].value_counts().head(40).to_dict() if len(df) else {},
        "by_bookmaker": df["bookmaker"].value_counts().to_dict() if len(df) else {},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1), "utf-8")
    return len(df)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "backfill"):
        s = sub.add_parser(name)
        s.add_argument("--bookmakers", default=DEFAULT_BOOKS)
        s.add_argument("--from", dest="start", default=None)
        s.add_argument("--to", dest="end", default=None)
    b = sub.choices["backfill"]
    b.add_argument("--out", default=str(OUT))
    b.add_argument("--max-fixtures", type=int, default=0)
    b.add_argument("--max-minutes", type=float, default=50.0)
    b.add_argument("--player-names", action="store_true",
                   help="one metered /v4/participants call for id->name")
    args = ap.parse_args(argv)
    try:
        return cmd_probe(args) if args.cmd == "probe" else cmd_backfill(args)
    except op.OddsPapiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
