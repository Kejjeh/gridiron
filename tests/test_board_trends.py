"""The board's usage-trend chips and report card (`gridiron.dashboard`).

Pins: every roster row carries the volume trend `gridiron.trends` computed
(or says there is no stat line); a week with no line is a gap, never a
zero bar; the report card renders the ledger, counts each week once, and
says plainly when nothing is graded; nothing new uses an inline `style`
attribute, which the page's Content-Security-Policy would silently drop.
Offline fixtures only.
"""
from __future__ import annotations

import csv
import re

from gridiron import dashboard as D
from gridiron import grading
from test_dashboard_cli import NOW, render  # noqa: F401  (shared fixture builder)


def _line(trend="RISING", weeks=((1, 5.0), (2, 6.0), (3, 9.0), (4, 12.0))):
    return {"gsis_id": "g", "games": len(weeks), "points": 40.0, "ppg": 10.0, "trend": trend,
            "trend_why": "opportunities 5.5 -> 10.5/game",
            "weeks": [{"week": w, "opportunities": o, "snap_pct": 70.0, "points": 9.0}
                      for w, o in weeks]}


def test_the_chip_shows_the_trend_and_its_reason_and_absence_is_not_a_trend():
    chip = D._trend_chip(_line("FALLING"))
    assert "t-falling" in chip and "Falling" in chip and "opportunities 5.5" in chip
    assert "No stat line" in D._trend_chip(None)
    assert "Too few games" in D._trend_chip(_line("TOO FEW GAMES"))


def test_a_week_without_a_line_is_a_gap_not_a_zero_bar():
    svg = D._sparkline(_line(weeks=((1, 5.0), (3, 9.0), (4, 12.0))), 4, 4)
    assert svg.count("class=\"bar") == 3 and svg.count("class=\"gap\"") == 1
    assert "week 2 no line" in svg and "week 4: 12 opportunities" in svg
    assert D._sparkline(None, 4, 4) == ""


def test_every_roster_row_carries_a_trend_on_the_page(tmp_path):
    rc, html, rec = render(tmp_path, "complete", "--grades-dir", str(tmp_path / "none"))
    assert rc == 0
    roster = html[html.index("id=\"roster\""):html.index("id=\"matchup\"")]
    rows = roster.count("<li class=\"rrow")
    assert rows == len(rec["roster"])
    assert roster.count("class=\"trend ") == rows
    # the fixture holds two weeks of box scores: a direction needs three
    assert "Too few games" in roster
    assert html.index("Action Desk") < html.index("Roster projections") < html.index("Report card")


def test_the_report_card_reads_the_ledger_and_says_when_nothing_is_graded(tmp_path):
    _, html, _ = render(tmp_path, "complete", "--grades-dir", str(tmp_path / "none"))
    assert "No week graded yet." in html

    grades = tmp_path / "grades"
    grades.mkdir()
    with (grades / "season2026.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["season", "week", "archive", "archive_built", "graded",
                                           "scorable", "agree", "direction_n", "direction_agree",
                                           "projection_mae", "projection_n", "shootout_pairs",
                                           "page_pairwise", "sleeper_pairwise", "graded_at"])
        w.writeheader()
        w.writerow({"season": 2026, "week": 3, "archive": "a.json", "graded": 24, "scorable": 0,
                    "agree": 0, "direction_n": 24, "direction_agree": 8, "projection_mae": 7.44,
                    "projection_n": 11, "shootout_pairs": 100, "page_pairwise": 0.6,
                    "sleeper_pairwise": 0.65, "graded_at": "2026-09-30T19:23:52+00:00"})
    _, html, _ = render(tmp_path / "b", "complete", "--grades-dir", str(grades))
    card = html[html.index("id=\"grades\""):]
    assert "No week graded yet." not in card
    assert "33%" in card and "8 of 24 calls" in card and "7.4" in card
    assert "Sleeper" in card and "65.0%" in card and "100 pairs" in card


def test_nothing_on_the_board_uses_an_inline_style_attribute(tmp_path):
    card = grading.report_card([{"season": "2026", "week": "3", "archive": "a", "graded": "4",
                                 "direction_n": "4", "direction_agree": "3",
                                 "shootout_pairs": "10", "page_pairwise": "0.7",
                                 "graded_at": "x"}], 2026)
    pieces = "".join(D._report_html(card)) + D._sparkline(_line(), 4, 4)
    _, html, _ = render(tmp_path, "complete", "--grades-dir", str(tmp_path / "none"))
    for text in (pieces, html):
        assert not re.search(r"<[^>]+\sstyle=", text), "an inline style attribute is blocked by CSP"
