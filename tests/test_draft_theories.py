import numpy as np
import pandas as pd
import pytest
from gridiron.draft_theories import (score_rows, stable_crosswalk, preseason_snapshot,
    attach_outcomes, check_split, mean_predictions, evaluate_distributions, team_games)


def test_scoring_normalizes_interceptions_lost_fumbles_and_two_points():
    stats=dict(passing_yards=100,passing_tds=1,passing_interceptions=2,
        rushing_yards=10,rushing_tds=0,receptions=2,receiving_yards=20,receiving_tds=0,
        sack_fumbles_lost=1,rushing_fumbles_lost=1,receiving_fumbles_lost=0,
        passing_2pt_conversions=1,rushing_2pt_conversions=1,receiving_2pt_conversions=0)
    assert score_rows(pd.DataFrame([stats]))[0] == 10


def test_ambiguous_crosswalk_is_not_name_resolved():
    mapping, ambiguous=stable_crosswalk(pd.DataFrame({'fantasypros_id':[1,1,2], 'gsis_id':['a','b','c']}))
    assert mapping=={2:'c'} and ambiguous=={1}


def test_snapshot_excludes_inseason_and_rest_of_season():
    rows=[dict(fp_page='ppr-cheatsheets',scrape_date='2020-09-03',id='1',pos='RB',ecr=5),
          dict(fp_page='ppr-cheatsheets',scrape_date='2020-09-10',id=1,pos='RB',ecr=1),
          dict(fp_page='/nfl/rankings/ros-ppr-overall.php',scrape_date='2020-09-04',id=2,pos='RB',ecr=1)]
    result=preseason_snapshot(pd.DataFrame(rows),2020,'2020-09-10')
    assert len(result)==1 and result.iloc[0].ecr==5


def test_zero_outcomes_kept_unmapped_not_silently_zeroed():
    d=attach_outcomes(pd.DataFrame({'id':[1,2,3]}),pd.Series({'a':50}),{1:'a',2:'b'})
    assert d.actual.iloc[:2].tolist()==[50,0]
    assert pd.isna(d.actual.iloc[2])


def test_duplicate_identity_rejected():
    with pytest.raises(ValueError):
        attach_outcomes(pd.DataFrame({'id':[1,2]}),pd.Series(dtype=float),{1:'a',2:'a'})


@pytest.mark.parametrize('train,test', [([2023],[2023]),([2024],[2023]),([],[2023])])
def test_invalid_chronological_split_rejected(train,test):
    with pytest.raises(ValueError):check_split(pd.DataFrame({'season':train}),pd.DataFrame({'season':test}))


def sample_data():
    return pd.DataFrame([dict(season=y,pos=p,rank=r,actual=300-50*np.log(r)+(y-2020)*3,
        gsis_id=f'{p}{r}') for y in (2020,2021,2022) for p in ('QB','RB','WR','TE') for r in range(1,13)])


def test_future_outcomes_cannot_change_forecasts():
    train=sample_data()
    test=train[train.season==2022].copy();test['season']=2023
    before=mean_predictions(train,test)
    changed=test.copy();changed['actual']=10000
    assert np.array_equal(before,mean_predictions(train,changed))
    a=evaluate_distributions(train,test)
    b=evaluate_distributions(train,changed)
    assert np.array_equal(a.expected,b.expected)
    assert np.array_equal(a.upside_predicted,b.upside_predicted)
    assert not np.array_equal(a.pinball,b.pinball)


def test_complete_game_grid_keeps_zero_production_positions():
    row=dict(season=2020,week=1,game_id='g',team='A',position='RB',season_type='REG',
        passing_yards=0,passing_tds=0,passing_interceptions=0,rushing_yards=10,rushing_tds=0,
        receptions=0,receiving_yards=0,receiving_tds=0,sack_fumbles_lost=0,rushing_fumbles_lost=0,
        receiving_fumbles_lost=0,passing_2pt_conversions=0,rushing_2pt_conversions=0,receiving_2pt_conversions=0)
    schedule=pd.DataFrame([dict(season=2020,week=1,game_id='g',home_team='A',away_team='B',game_type='REG')])
    games=team_games(pd.DataFrame([row]),schedule)
    assert len(games)==6 and games.points.sum()==1 and (games.points==0).sum()==5


def test_schedule_features_do_not_use_target_season_outcomes():
    from gridiron.draft_theories import schedule_features
    games=pd.DataFrame([dict(season=y,week=w,pos=p,team=t,opponent=o,points=10+y%3)
        for y in range(2019,2026) for w in range(1,18) for p in ('RB','WR','TE')
        for t,o in [('A','B'),('B','A')]])
    a=schedule_features(games)
    games.loc[games.season==2025,'points']=10000
    b=schedule_features(games)
    columns=['team','pos','window','baseline','sos']
    pd.testing.assert_frame_equal(a.loc[a.season==2025,columns],b.loc[b.season==2025,columns])
    assert not a.loc[a.season==2025,'actual'].equals(b.loc[b.season==2025,'actual'])
