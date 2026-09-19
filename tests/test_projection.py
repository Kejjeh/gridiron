"""The baseline projection: transparent, chronological, abstaining.

Built from the committed fixtures; nothing here touches the network or the
owner's league. The invariants pinned:

  1. The mean is the sum of its printed components — a reader can recompute
     it from the page.
  2. Evidence is cut at `through_week` BEFORE aggregation: later weeks
     cannot move an earlier projection.
  3. It abstains (mean=None, with a reason) rather than guessing: no box
     score, DST, no pooled QB/K prior, unknown position.
  4. Rule #6: usage is shrunk toward a role prior with the documented n0;
     the player's own efficiency is never read.
  5. The line multiplier is 1.0 with a caveat when no line is posted.
  6. `withheld` zeroes the number but keeps the model mean visible.
  7. Rule #5 gate: an unvalidated feature in FEATS fails the import.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

from gridiron.ids import Crosswalk
from gridiron.projection import (
    BASELINE_LABEL, PROJECTABLE, TEAM_VOLUME_PLAUSIBILITY, build_evidence, project,
)
from gridiron.shrinkage import N0_IN_SEASON, TARGET_SHARE_PRIORS, TEAM_TARGETS_PER_GAME
from gridiron.usage import player_weeks
from gridiron.vegas import points_per_target, receiving_efficiency_multiplier

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def weeks() -> pd.DataFrame:
    cw = Crosswalk.from_csv(FIXTURES / "crosswalk_small.csv")
    w = pd.concat([pd.read_csv(FIXTURES / "weekly_offense_wk1.csv"),
                   pd.read_csv(FIXTURES / "weekly_kickers_wk1.csv")], ignore_index=True)
    wk2 = w.copy()
    wk2["week"] = 2
    for c in ("targets", "receptions", "receiving_yards", "carries", "rushing_yards"):
        wk2[c] = wk2[c] * 3           # a loud week 2, so leakage would show
    return player_weeks(pd.concat([w, wk2], ignore_index=True),
                        pd.read_csv(FIXTURES / "snaps_wk1.csv"), cw)


HENRY = "00-0032764"       # RB, BAL, in the fixtures
JEFFERSON = "00-0036322"   # WR, MIN
WENTZ = "00-0032950"       # QB, MIN
BOSWELL = "00-0031136"     # K, PIT


def test_the_mean_is_the_sum_of_its_printed_components(weeks):
    ev = build_evidence(weeks, through_week=1)
    p = project(ev.players[HENRY], position="RB", week=2, implied_total=24.0, evidence=ev)
    assert p.usable
    assert p.mean == pytest.approx(sum(c.points for c in p.components), abs=1e-3)
    for c in p.components:
        assert c.points == pytest.approx(c.volume * c.rate * c.multiplier, abs=1e-6)
    assert p.label == BASELINE_LABEL and "UNVALIDATED" in p.label
    text = p.explain()
    assert "receiving" in text and "rushing" in text and "±" in text


def test_evidence_is_cut_before_aggregation_so_later_weeks_cannot_leak(weeks):
    ev1 = build_evidence(weeks, through_week=1)
    ev1_again = build_evidence(weeks.loc[weeks["week"] <= 1], through_week=None)
    for gid in (HENRY, JEFFERSON, WENTZ):
        a = project(ev1.players[gid], position=ev1.players[gid].position, week=2,
                    implied_total=22.0, evidence=ev1)
        b = project(ev1_again.players[gid], position=ev1_again.players[gid].position,
                    week=2, implied_total=22.0, evidence=ev1_again)
        assert a.mean == pytest.approx(b.mean)
    # and the tripled week 2 DOES move a through-week-2 projection
    ev2 = build_evidence(weeks, through_week=2)
    a = project(ev1.players[JEFFERSON], position="WR", week=3, implied_total=22.0, evidence=ev1)
    b = project(ev2.players[JEFFERSON], position="WR", week=3, implied_total=22.0, evidence=ev2)
    # (compare the shrunk share, not the mean: the thin fixture frame trips
    # the team-volume plausibility floor differently at each cut)
    assert b.inputs["target_share_used"] > a.inputs["target_share_used"]


@pytest.mark.parametrize("position,reason", [
    ("DST", "no projection model"),
    ("DEF", "team defense"),
    ("OL", "no projection model"),
    ("", "unknown position"),
])
def test_it_abstains_on_positions_it_cannot_project(weeks, position, reason):
    ev = build_evidence(weeks, through_week=1)
    p = project(None, position=position, week=2, implied_total=22.0, evidence=ev)
    assert p.mean is None and p.sd is None
    assert any(reason in r for r in p.reasons)
    assert p.explain().startswith("ABSTAINED")


def test_it_abstains_with_no_admissible_box_score(weeks):
    ev = build_evidence(weeks, through_week=1)
    p = project(None, position="WR", week=2, implied_total=22.0, evidence=ev)
    assert p.mean is None and "no admissible box scores through week 1" in p.reasons[0]


def test_it_abstains_for_a_quarterback_when_no_pooled_prior_can_be_measured(weeks):
    only_wr = weeks.loc[weeks["position"] == "WR"]
    ev = build_evidence(only_wr, through_week=1)
    fake_qb = build_evidence(weeks, through_week=1).players[WENTZ]
    p = project(fake_qb, position="QB", week=2, implied_total=22.0, evidence=ev)
    assert p.mean is None and "pooled quarterback prior" in p.reasons[0]
    k = build_evidence(weeks, through_week=1).players[BOSWELL]
    p = project(k, position="K", week=2, implied_total=22.0, evidence=ev)
    assert p.mean is None and "kicker" in p.reasons[0]


def test_usage_is_shrunk_toward_the_role_prior_with_the_documented_n0(weeks):
    ev = build_evidence(weeks, through_week=1)
    pe = ev.players[JEFFERSON]
    p = project(pe, position="WR", week=2, implied_total=None, evidence=ev)
    role = ev.roles[JEFFERSON]
    prior_mean, _ = TARGET_SHARE_PRIORS[role]
    n = pe.team_targets_in_games
    n0 = N0_IN_SEASON["wr_te_target_share"]
    expected_share = (pe.targets + prior_mean * n0) / (n + n0)
    assert p.inputs["target_share_used"] == pytest.approx(expected_share, abs=1e-4)
    assert p.inputs["share_weight"] == pytest.approx(n / (n + n0), abs=1e-3)
    rec = next(c for c in p.components if c.name == "receiving")
    # efficiency is the POSITIONAL prior, never the player's own yards/target
    assert rec.rate == pytest.approx(points_per_target("WR"))
    assert rec.multiplier == 1.0
    assert any("no market line" in r for r in p.reasons)


def test_the_line_moves_efficiency_not_volume(weeks):
    ev = build_evidence(weeks, through_week=1)
    pe = ev.players[JEFFERSON]
    lo = project(pe, position="WR", week=2, implied_total=19.0, evidence=ev)
    hi = project(pe, position="WR", week=2, implied_total=25.0, evidence=ev)
    rlo = next(c for c in lo.components if c.name == "receiving")
    rhi = next(c for c in hi.components if c.name == "receiving")
    assert rlo.volume == pytest.approx(rhi.volume)           # volume untouched
    assert rhi.multiplier == pytest.approx(receiving_efficiency_multiplier(25.0))
    assert hi.mean > lo.mean


def test_an_implausibly_thin_team_frame_falls_back_to_the_league_mean_and_says_so(weeks):
    ev = build_evidence(weeks, through_week=1)
    pe = ev.players[JEFFERSON]
    team = ev.teams[pe.team]
    assert team.targets_per_game < TEAM_VOLUME_PLAUSIBILITY * TEAM_TARGETS_PER_GAME, \
        "fixture no longer thin; pick another team for this test"
    p = project(pe, position="WR", week=2, implied_total=22.0, evidence=ev)
    assert p.inputs["team_targets_per_game"] == TEAM_TARGETS_PER_GAME
    assert any("plausibility floor" in r for r in p.reasons)


def test_withheld_keeps_the_model_mean_visible(weeks):
    ev = build_evidence(weeks, through_week=1)
    p = project(ev.players[HENRY], position="RB", week=2, implied_total=24.0, evidence=ev)
    w = p.withheld("BYE week: projected 0")
    assert w.mean == 0.0 and w.sd == 0.0 and w.usable
    assert w.inputs["model_mean"] == p.mean
    assert "BYE week" in w.reasons[-1]


def test_role_comes_from_usage_not_the_tag(weeks):
    ev = build_evidence(weeks, through_week=1)
    # Within a team, the WR with the most targets is WR1 regardless of any tag.
    by_team: dict[str, list] = {}
    for gid, pe in ev.players.items():
        if pe.position == "WR":
            by_team.setdefault(pe.team, []).append((pe.targets, gid))
    for team, rows in by_team.items():
        top = max(rows)[1]
        assert ev.roles[top] == "WR1"


def test_sd_is_positional_cv_times_mean_with_a_floor(weeks):
    ev = build_evidence(weeks, through_week=1)
    p = project(ev.players[HENRY], position="RB", week=2, implied_total=24.0, evidence=ev)
    assert p.sd == pytest.approx(max(p.mean * 0.55, 1.5), abs=1e-3)


def test_every_projectable_position_is_covered():
    assert set(PROJECTABLE) == {"QB", "RB", "WR", "TE", "K"}


def test_the_rule_5_gate_fails_the_import_for_an_unvalidated_feature(monkeypatch):
    name = "gridiron.models.validated_signals"
    module = importlib.import_module(name)
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "assert not _unvalidated" in src
    # Simulate a feature added without evidence: the module must not import.
    bad = src.replace("FEATS: tuple[str, ...] = ()", 'FEATS: tuple[str, ...] = ("snap_trend",)')
    ns: dict = {"__name__": "gate_probe"}
    with pytest.raises(AssertionError, match="rule #5"):
        exec(compile(bad, "gate_probe", "exec"), ns)
