"""Growing the odds-name table (`scripts/ingest/odds_names.py`), decision 1.

Pins: only an EXACT, unique, position-consistent match on one of the
event's two teams that week is appended; no fuzzy match and no suffix
stripping; an unpublished roster proposes nothing; the table stays valid.
Fictional players and ids only.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pandas as pd

from gridiron import props as P

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ON = _load("odds_names_cli", "scripts/ingest/odds_names.py")


def ou(player, point=4.5):
    return [{"name": "Over", "description": player, "price": -110, "point": point},
            {"name": "Under", "description": player, "price": -110, "point": point}]


def write_snapshot(raw: Path, week: int, game_id: str, markets: dict) -> None:
    payload = {"id": "evtX", "commence_time": "2026-11-08T18:00:00Z",
               "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
               "bookmakers": [{"key": "book_alpha", "markets": [
                   {"key": k, "outcomes": v} for k, v in markets.items()]}]}
    folder = raw / "season2026" / f"week{week:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{game_id}__20261108T163000Z.json").write_text(json.dumps(
        {"meta": {"game_id": game_id, "week": week, "season": 2026,
                  "taken_at": "2026-11-08T16:30:00+00:00"}, "payload": payload}), "utf-8")


ROSTER = pd.DataFrame([
    # week, team, full_name, gsis_id, position
    (9, "KC", "Pat Fixture", "00-0099001", "QB"),
    (9, "BUF", "Wes Fixture", "00-0099002", "WR"),
    (9, "BUF", "Sam Fixture", "00-0099004", "WR"),          # the snapshot says "Sam Fixture Jr."
    (9, "KC", "Lee Fixture", "00-0099005", "RB"),
    (9, "BUF", "Lee Fixture", "00-0099006", "TE"),          # same name, other team
    (9, "KC", "Quin Fixture", "00-0099007", "QB"),          # a QB priced on receptions
    (9, "NYJ", "Ray Fixture", "00-0099008", "WR"),          # not on either team
    (8, "BUF", "Wes Fixture", "00-0099002", "WR"),
], columns=["week", "team", "full_name", "gsis_id", "position"])


def setup(tmp_path, week=9):
    raw = tmp_path / "raw"
    write_snapshot(raw, week, f"2026_{week:02d}_BUF_KC", {
        "player_pass_yds": ou("Pat Fixture", 245.5),
        "player_receptions": ou("Wes Fixture") + ou("Sam Fixture Jr.") + ou("Lee Fixture")
        + ou("Quin Fixture") + ou("Ray Fixture") + ou("pat fixture")})
    table = tmp_path / "names.csv"
    table.write_text("odds_name,team,gsis_id,basis,added\n", encoding="utf-8")
    return raw, table


def test_only_exact_unique_position_consistent_names_are_appended(tmp_path):
    raw, table = setup(tmp_path)
    out = io.StringIO()
    rows = ON.propose(season=2026, raw_root=raw, table_path=table, rosters=ROSTER, weeks=None,
                      today="2026-11-09", dry_run=False, out=out)
    assert [(r.odds_name, r.team, r.gsis_id, r.basis) for r in rows] == [
        ("Wes Fixture", "BUF", "00-0099002", "exact"),
        ("Pat Fixture", "KC", "00-0099001", "exact")]
    text = out.getvalue()
    assert "'Sam Fixture Jr.'" in text and "no player of that exact name" in text   # no stripping
    assert "more than one player of that exact name" in text                        # Lee
    assert "position QB not consistent with player_receptions" in text              # Quin
    assert "'pat fixture'" in text                                                  # no case folding
    assert "00-0099008" not in text                     # a player on neither team is no candidate
    t = P.IdentityTable.read(table)
    assert t.resolve("Wes Fixture", ("KC", "BUF")).basis == "exact"
    assert t.resolve("Sam Fixture Jr.", ("KC", "BUF")).gsis_id is None
    # a second run appends nothing new and the file stays valid
    again = ON.propose(season=2026, raw_root=raw, table_path=table, rosters=ROSTER, weeks=None,
                       today="2026-11-10", dry_run=False, out=io.StringIO())
    assert again == [] and len(P.IdentityTable.read(table)) == 2


def test_a_dry_run_and_a_reviewed_row_change_nothing(tmp_path):
    raw, table = setup(tmp_path)
    table.write_text("odds_name,team,gsis_id,basis,added\n"
                     "Sam Fixture Jr.,BUF,00-0099004,reviewed,2026-11-09\n", encoding="utf-8")
    before = table.read_text("utf-8")
    rows = ON.propose(season=2026, raw_root=raw, table_path=table, rosters=ROSTER, weeks=None,
                      today="2026-11-09", dry_run=True, out=io.StringIO())
    assert {r.odds_name for r in rows} == {"Wes Fixture", "Pat Fixture"}
    assert table.read_text("utf-8") == before


def test_an_unpublished_roster_week_proposes_nothing(tmp_path):
    raw, table = setup(tmp_path, week=10)            # the frame holds weeks 8 and 9 only
    out = io.StringIO()
    rows = ON.propose(season=2026, raw_root=raw, table_path=table, rosters=ROSTER, weeks=None,
                      today="2026-11-16", dry_run=False, out=out)
    assert rows == []
    assert "week 10 roster NOT published; candidates from week 9, nothing appended" in out.getvalue()


def test_check_counts_the_table_by_basis(tmp_path, capsys):
    table = tmp_path / "names.csv"
    table.write_text("odds_name,team,gsis_id,basis,added\n"
                     "Pat Fixture,KC,00-0099001,exact,2026-11-09\n", encoding="utf-8")
    assert ON.main(["check", "--table", str(table)]) == 0
    assert "1 row(s); exact 1, reviewed 0" in capsys.readouterr().out


def test_propose_reads_the_local_name_lists_too(tmp_path):
    """`pull_props.py names` stores one cheap market per game under
    raw/names/; propose treats its names exactly like a snapshot's."""
    raw = tmp_path / "raw"
    folder = raw / "names" / "season2026" / "week09"
    folder.mkdir(parents=True)
    payload = {"id": "evtX", "commence_time": "2026-11-08T18:00:00Z",
               "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
               "bookmakers": [{"key": "book_alpha", "markets": [{"key": "player_anytime_td",
                               "outcomes": [{"name": "Yes", "description": n, "price": 150}
                                            for n in ("Pat Fixture", "Wes Fixture",
                                                      "Sam Fixture Jr.")]}]}]}
    (folder / "2026_09_BUF_KC__20261106T120000Z.json").write_text(json.dumps(
        {"meta": {"game_id": "2026_09_BUF_KC", "week": 9, "season": 2026,
                  "purpose": "names only"}, "payload": payload}), "utf-8")
    table = tmp_path / "names.csv"
    table.write_text("odds_name,team,gsis_id,basis,added\n", encoding="utf-8")
    out = io.StringIO()
    rows = ON.propose(season=2026, raw_root=raw, table_path=table, rosters=ROSTER, weeks=None,
                      today="2026-11-06", dry_run=False, out=out)
    assert {(r.odds_name, r.gsis_id) for r in rows} == {("Pat Fixture", "00-0099001"),
                                                         ("Wes Fixture", "00-0099002")}
    assert "'Sam Fixture Jr.'" in out.getvalue()          # still no suffix stripping
    assert P.read_raw_snapshots(raw, 2026) == {}          # and still never a snapshot
