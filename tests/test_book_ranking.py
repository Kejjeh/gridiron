"""The book ranking (`scripts/research/book_ranking.py`) on fictional
snapshots: line error, Brier and log loss of the de-vigged P(over), distance
from the consensus; pushes, void props and unmatched names left out and
counted; under four weeks it says so and still runs. Fictional players,
books and ids only."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from gridiron import props as P

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BR = _load("book_ranking_cli", "scripts/research/book_ranking.py")
WR, RB = "00-0099002", "00-0099009"


def ou(player, point, over=-110, under=-110):
    return [{"name": "Over", "description": player, "price": over, "point": point},
            {"name": "Under", "description": player, "price": under, "point": point}]


def write(raw: Path, week: int, books: dict) -> None:
    payload = {"id": f"evt{week}", "commence_time": "2026-11-08T18:00:00Z",
               "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
               "bookmakers": [{"key": k, "markets": [{"key": m, "outcomes": o}
                                                     for m, o in v.items()]}
                              for k, v in books.items()]}
    folder = raw / "season2026" / f"week{week:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"2026_{week:02d}_BUF_KC__20261108T163000Z.json").write_text(json.dumps(
        {"meta": {"game_id": f"2026_{week:02d}_BUF_KC", "week": week, "season": 2026},
         "payload": payload}), "utf-8")


def setup(tmp_path):
    raw = tmp_path / "raw"
    write(raw, 9, {
        "book_alpha": {"player_reception_yds": ou("Wes Fixture", 60.5),
                       "player_rush_yds": ou("Rob Fixture", 50.0) + ou("Nobody Fixture", 20.5),
                       "player_anytime_td": [
                           {"name": "Yes", "description": "Wes Fixture", "price": -150},
                           {"name": "No", "description": "Wes Fixture", "price": 120}]},
        "book_beta": {"player_reception_yds": ou("Wes Fixture", 70.5)}})
    write(raw, 10, {"book_alpha": {"player_reception_yds": ou("Wes Fixture", 55.5)}})
    table = tmp_path / "names.csv"
    table.write_text("odds_name,team,gsis_id,basis,added\n"
                     f"Wes Fixture,BUF,{WR},exact,2026-11-09\n"
                     f"Rob Fixture,KC,{RB},reviewed,2026-11-09\n", encoding="utf-8")
    actuals = {(WR, 9): {**dict.fromkeys(BR._STATS, 0.0), "receiving_yards": 65.0,
                         "scrimmage_tds": 1.0, "kicking_points": 0.0},
               (RB, 9): {**dict.fromkeys(BR._STATS, 0.0), "rushing_yards": 50.0,
                         "scrimmage_tds": 0.0, "kicking_points": 0.0}}
    # week 10: the receiver did not play -> his prop is void
    return raw, table, actuals


def test_the_books_are_graded_on_error_calibration_and_consensus_gap(tmp_path):
    raw, table, actuals = setup(tmp_path)
    rows, skipped = BR.graded_lines(P.read_raw_snapshots(raw, 2026), P.IdentityTable.read(table),
                                    actuals)
    g = {(r["market"], r["book"]): r for r in BR.grade(rows)}
    alpha, beta = g[("player_reception_yds", "book_alpha")], g[("player_reception_yds", "book_beta")]
    cons = g[("player_reception_yds", "consensus")]
    assert (alpha["n"], alpha["line_mae"], alpha["line_bias"]) == (1, 4.5, 4.5)
    assert (beta["line_mae"], beta["line_bias"]) == (5.5, -5.5)
    assert alpha["brier"] == beta["brier"] == 0.25 and cons["line_bias"] == -0.5
    assert alpha["gap_line"] == beta["gap_line"] == 5.0 and cons["gap_line"] == 0.0
    assert alpha["log_loss"] == pytest.approx(math.log(2), abs=1e-4)
    td = g[("player_anytime_td", "book_alpha")]
    p = 0.6 / (0.6 + 100 / 220)
    assert td["brier"] == pytest.approx((1 - p) ** 2, abs=1e-4) and td["line_mae"] is None
    assert ("player_rush_yds", "book_alpha") not in g          # its only line pushed
    assert skipped == {"push": 1, "name unmatched": 1,
                       "no box score (did not play: the prop is void)": 1}


def test_under_four_weeks_it_says_so_and_still_runs(tmp_path, capsys):
    raw, table, actuals = setup(tmp_path)
    frame = pd.DataFrame([{"player_id": gid, "week": wk, "season_type": "REG", **s}
                          for (gid, wk), s in actuals.items()])
    path = tmp_path / "weekly.csv"
    frame.to_csv(path, index=False)
    out = tmp_path / "grades.json"
    assert BR.main(["--raw", str(raw), "--table", str(table), "--actuals", str(path),
                    "--markdown", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "needs 4+ collected weeks (have 2); running anyway" in text
    assert "| player_reception_yds | book_alpha |" in text
    for leak in ("Fixture", WR, RB, "-110", "60.5"):
        assert leak not in text, f"the ranking printed {leak!r}"
    blob = json.loads(out.read_text("utf-8"))
    assert blob["weeks"] == 2 and blob["grades"]


def test_an_empty_archive_grades_nothing(tmp_path, capsys):
    assert BR.main(["--raw", str(tmp_path / "none")]) == 0
    assert "nothing to grade" in capsys.readouterr().out
