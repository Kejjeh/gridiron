"""Offline weekly betting-line incremental test; never a draft-date feature backtest."""
import json
import hashlib
import numpy as np
import pandas as pd
from gridiron.paths import RESEARCH_CACHE, OUTPUTS, DOCS
from gridiron.draft_theories import score_rows, block_interval
from gridiron.vegas import implied_totals_from_nflverse
from gridiron.league_config import KICKING_SCORING

cache=RESEARCH_CACHE/'draft_theories'
stats=pd.concat([pd.read_parquet(cache/f'stats_{y}.parquet') for y in range(2019,2026)])
stats=stats[stats.season_type.eq('REG')].copy()
stats['points']=score_rows(stats)
# Kicker scoring uses the shared league weights, including 60+ yard field goals.
k=stats.position.eq('K');ks=stats.loc[k].copy()
map_stats={'fgm_0_19':'fg_made_0_19','fgm_20_29':'fg_made_20_29','fgm_30_39':'fg_made_30_39','fgm_40_49':'fg_made_40_49','fgm_50p':'long_fg','xpm':'pat_made','fgmiss':'fg_missed','xpmiss':'pat_missed'}
ks['long_fg']=ks.fg_made_50_59.fillna(0)+ks['fg_made_60_'].fillna(0)
stats.loc[k,'points']=sum(ks[column].fillna(0)*KICKING_SCORING[key] for key,column in map_stats.items())
stats=stats[stats.position.isin(['QB','RB','WR','TE','K'])]
points=stats.groupby(['game_id','team','position']).points.sum().reset_index()
schedules=pd.read_parquet(cache/'schedules.parquet')
schedules=schedules[schedules.game_type.eq('REG')&schedules.home_score.notna()&schedules.away_score.notna()]
rows=[]
for _,g in schedules.iterrows():
    home,away=implied_totals_from_nflverse(g.total_line,g.spread_line) if pd.notna(g.total_line) and pd.notna(g.spread_line) else (np.nan,np.nan)
    for team,opp,own,other in [(g.home_team,g.away_team,home,away),(g.away_team,g.home_team,away,home)]:
        for pos in ['QB','RB','WR','TE','K']:rows.append(dict(game_id=g.game_id,season=g.season,week=g.week,team=team,opponent=opp,position=pos,implied=own,opp_implied=other))
d=pd.DataFrame(rows).merge(points,on=['game_id','team','position'],how='left',validate='one_to_one')
d['points']=d.points.fillna(0);d=d.sort_values(['season','week','game_id'])
d['own_form']=d.groupby(['team','position']).points.transform(lambda s:s.shift(1).rolling(4,min_periods=4).mean())
d['allowed_form']=d.groupby(['opponent','position']).points.transform(lambda s:s.shift(1).rolling(4,min_periods=4).mean())
# allowed_form describes THIS row's opponent's preceding defensive games.
raw_count=len(d);d=d.dropna(subset=['own_form','allowed_form','implied','opp_implied'])
results=[];summary=[]
for pos in ['QB','RB','WR','TE','K']:
    group=d[d.position==pos]
    for year in [2023,2024,2025]:
        train=group[(group.season>=2020)&(group.season<year)];test=group[group.season==year]
        assert train.season.max()<test.season.min()
        for model,cols in [('baseline',['own_form','allowed_form']),('plus_lines',['own_form','allowed_form','implied','opp_implied'])]:
            x=np.column_stack([np.ones(len(train)),train[cols]]);xt=np.column_stack([np.ones(len(test)),test[cols]])
            pred=xt@np.linalg.lstsq(x,train.points,rcond=None)[0]
            for (_,r),p in zip(test.iterrows(),pred):results.append(dict(position=pos,season=year,game_id=r.game_id,team=r.team,model=model,actual=r.points,predicted=p,error_sq=(r.points-p)**2))
    valid=group[group.season.isin([2023,2024,2025])]
    summary.append(dict(position=pos,correlation=float(valid[['implied','points']].corr().iloc[0,1]),n=len(valid)))
r=pd.DataFrame(results)
for row in summary:
    a=r[r.position==row['position']].groupby(['season','model']).error_sq.mean().unstack()
    delta=a.plus_lines-a.baseline
    row.update(baseline_rmse=float(np.sqrt(a.baseline.mean())),candidate_rmse=float(np.sqrt(a.plus_lines.mean())),better_seasons=int((delta<0).sum()),delta_mse_interval=block_interval(delta))
r.to_csv(OUTPUTS/'vegas_weekly_predictions.csv',index=False)
manifest={'scope':'Weekly retrospective closing lines, not preseason or draft-time inputs','baseline':'Previous four team offensive and opponent defensive games; lagged before current game','results':summary,'excluded_rows':raw_count-len(d),'production_approved':False,'hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [cache/'schedules.parquet']+[cache/f'stats_{y}.parquet' for y in range(2019,2026)]}}
(OUTPUTS/'vegas_weekly_results.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
lines=['# Betting lines and fantasy production: weekly test','','Not approved as a draft-time feature. These are historical closing lines, including future weeks unavailable on draft night.','',
'Fit 2020-2022 and test 2023; expand training for 2024 and 2025. Inputs are lagged previous-four-game team offense and opponent defense. The candidate adds own and opposing implied points. Test-year earlier games may enter rolling inputs after they happen; test-year outcomes never enter coefficient fitting. Team-position production uses core league half-PPR scoring and shared kicker weights. Missing lines or insufficient rolling history are excluded on common support.','',
'| Position | Test team-games | Correlation | Baseline RMSE | With lines RMSE | Better seasons |','|---|---:|---:|---:|---:|---:|']
for x in summary:lines.append(f"| {x['position']} | {x['n']} | {x['correlation']:.3f} | {x['baseline_rmse']:.3f} | {x['candidate_rmse']:.3f} | {x['better_seasons']}/3 |")
lines+=['','These are team-position totals, not individual player projections. No D/ST scoring test is claimed. The baseline does not contain every production feature; three season clusters, correlated team-games, multiple positions, and historical closing-line timing limit inference. No live weighting is promoted and no betting lines are displayed in the draft room.','',
'Rerun: `$env:PYTHONPATH="src"` then `.venv/Scripts/python.exe scripts/research/test_vegas_weekly.py`. Raw schedules and statistics come from nflverse; input hashes and per-game forecasts accompany the report.']
(DOCS/'research'/'VEGAS_WEEKLY_RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps(summary,indent=2))
