import pytest
from gridiron.personnel import build_personnel, pff_index, validate_notes

NOW="2026-09-08T23:59:59+00:00"

def player(pid="a",team="DET",name="Same Name",**extra):
    return dict(gsis_id=pid,team=team,full_name=name,position="T",status="ACT",espn_id=pid,pff_id=pid,**extra)

def depth(pid="a",team="DET",slot="1",rank="1",date="2026-09-08T10:00:00Z"):
    return dict(gsis_id=pid,espn_id=pid,player_name="Chart Name",team=team,pos_abb="LT",
                pos_grp="3WR 1TE",pos_slot=slot,pos_rank=rank,dt=date)

def build(old=None,new=None,chart=None):
    return build_personnel(old or [],new or [player()],chart or [],{"teams":{}},as_of=NOW)

def test_transfers_join_by_id_not_equal_name():
    old=[player("a","BUF"),player("b","DET")]
    new=[player("a","DET"),player("b","DET")]
    t=build(old,new)["DET"]
    assert [p["id"] for p in t["arrivals"]]==["a"]
    assert t["arrivals"][0]["previous_team"]=="BUF"

def test_missing_prior_record_not_called_rookie_or_transfer():
    p=build()["DET"]["arrivals"][0]
    assert p["previous_team"] is None
    assert p["evidence"]=="Not in prior roster snapshot"

def test_departure_absence_not_given_invented_destination():
    out=build([player("b")],[player()])["DET"]["departures"][0]
    assert out["next_team"] is None

def test_latest_chart_per_team_ignores_future_and_stale_rows():
    chart=[depth(date="2026-09-01T10:00:00Z"),depth("b"),depth("c",date="2026-09-09T10:00:00Z")]
    t=build(new=[player("b")],chart=chart)["DET"]
    assert len(t["starters"])==1
    assert t["starters"][0]["id"]=="b"

def test_each_chart_slot_uses_its_top_rank_not_global_rank_one():
    chart=[depth(slot="1"),depth("b",slot="2",rank="2")]
    assert len(build(new=[player(),player("b")],chart=chart)["DET"]["starters"])==2

def test_conflicting_ids_do_not_receive_grades_or_prior_history():
    d=depth();d["espn_id"]="different"
    t=build_personnel([player()], [player()], [d], {"teams":{}},as_of=NOW,
                      grades={"a":{"run_block_grade":90}})["DET"]
    assert t["identity_warnings"]==1
    assert t["starters"][0]["id"] is None
    assert t["starters"][0]["pff"] is None

def test_duplicate_roster_ids_rejected():
    with pytest.raises(ValueError,match="Duplicate"):build(new=[player(),player()])

def grade(**kw):
    row=dict(season="2025",gsis_id="a",pff_id="",position="LT",
             run_block_grade="70",pass_block_grade="80",run_block_snaps="200",
             pass_block_snaps="300",source_url="https://www.pff.com/grades",
             published_at="2026-01-06T12:00:00+00:00")
    row.update(kw)
    return row

def test_pff_components_keep_snap_counts_and_support_stable_crosswalk():
    out=pff_index([grade(gsis_id="",pff_id="a")],[player()],season=2026,as_of=NOW)
    assert out["a"]["pass_block_grade"]==80
    assert out["a"]["run_block_snaps"]==200

@pytest.mark.parametrize("change",[
    {"season":"2026"},{"published_at":"2026-09-09T00:00:00+00:00"},
    {"run_block_grade":"nan"},{"pass_block_grade":"101"},{"run_block_snaps":"0"},
    {"gsis_id":"unknown"},{"source_url":"javascript:bad"}
])
def test_pff_rejects_invalid_or_future_records(change):
    with pytest.raises(ValueError):
        pff_index([grade(**change)],[player()],season=2026,as_of=NOW)

def test_ambiguous_pff_crosswalk_rejected():
    second=player("b");second["pff_id"]="a"
    with pytest.raises(ValueError,match="crosswalk"):
        pff_index([grade(gsis_id="",pff_id="a")],[player(),second],season=2026,as_of=NOW)

def test_future_coaching_notes_rejected():
    notes={"teams":{"DET":[dict(kind="scheme",text="future",published="2026-09-09T00:00:00+00:00",
                               source_url="https://www.detroitlions.com")]}}
    with pytest.raises(ValueError,match="Future"):validate_notes(notes,NOW)
