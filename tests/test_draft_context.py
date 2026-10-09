"""Context safeguards: chronological data, matchup direction, gaps and aliases."""
import pytest
from gridiron.draft_context import build_context

def snapshot():
    return {"season":2025,"published":"2026-01-06T15:51:00+00:00","teams":[
        {"team":"LAR","pass_rush":50,"run_stop":40,"pass_block":70,"run_block":75,"pass_rank":1,"run_rank":2},
        {"team":"DET","pass_rush":30,"run_stop":20,"pass_block":60,"run_block":65,"pass_rank":20,"run_rank":25}]}

def game(week=1, **extra):
    return {"season":2026,"game_type":"REG","week":week,"home_team":"LA","away_team":"DET",**extra}

def build(s=None,g=None,**kw):
    return build_context(s or snapshot(),g if g is not None else [game()],
                         season=2026,as_of=kw.get("as_of","2026-09-08T20:00:00+00:00"))

def test_home_away_aliases_and_opponent_not_own_strength():
    result=build()
    assert result["LA"]["run_block"]["rate"]==75
    assert result["LA"]["periods"]["early"]["run_stop"]==20
    assert result["DET"]["periods"]["early"]["pass_rush"]==50
    assert result["DET"]["periods"]["early"]["opponents"][0]["venue"]=="away"
    assert all(t["adjustment"]==0 for t in result.values())

def test_windows_byes_and_postseason_exclusion():
    result=build(g=[game(w) for w in [1,3,14,15,17,18]]+[game(2,game_type="POST")])
    assert result["LA"]["periods"]["early"]["games"]==2
    assert result["LA"]["periods"]["regular"]["games"]==3
    assert result["LA"]["periods"]["playoffs"]["games"]==2

def test_missing_opponent_metric_is_not_averaged_away():
    result=build(g=[game(),game(2,away_team="BUF")])
    early=result["LA"]["periods"]["early"]
    assert early["pass_rush"] is None
    assert early["pass_rush_coverage"]==1
    assert early["games"]==2

def test_future_publications_or_same_season_metrics_rejected():
    with pytest.raises(ValueError,match="Future"):
        build(as_of="2026-01-01T00:00:00+00:00")
    s=snapshot();s["season"]=2026
    with pytest.raises(ValueError,match="Future"):build(s=s)

def test_duplicate_team_week_rejected():
    with pytest.raises(ValueError,match="Duplicate"):build(g=[game(),game()])

def test_missing_metrics_are_unavailable_and_invalid_rates_fail():
    s=snapshot();s["teams"][0]["run_block"]=None
    assert build(s=s)["LA"]["run_block"]["rate"] is None
    s["teams"][0]["run_block"]=101
    with pytest.raises(ValueError,match="win rate"):build(s=s)

def test_game_results_never_change_context():
    assert build(g=[game(home_score=99,away_score=0)])==build(g=[game(home_score=0,away_score=99)])

def test_duplicate_alias_team_rejected():
    s=snapshot();s["teams"].append({**s["teams"][0],"team":"LA"})
    with pytest.raises(ValueError,match="Duplicate"):build(s=s)
